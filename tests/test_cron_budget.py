"""The other two levels of the spend cap: one dispatch, and one day.

The run-level cap bounds a single loop. Neither of the other two is derivable from it. A job can sit
inside its own ceiling on every dispatch and still spend the month by tea-time, which is what the
daily aggregate is for; and a daily aggregate cannot stop the single runaway retry loop that empties
the balance between two ticks, which is what the per-job ceiling is for.

Two decisions are recorded here as behaviour rather than as prose. A refusal is **not a failure** —
it gets its own status and does not climb the failure counter, because "the money said no" and "this
job is broken" need opposite responses. And a job can be marked **critical** to escape the daily cap,
because a position guardian silenced at 2 p.m. until midnight costs more than it saves.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from chimera.api.usage import UsageRecord, append_usage, spent_today
from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler import CronStore, Scheduler
from chimera.scheduler.models import CronJob

TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


def _scheduler(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "cron.json"))


def _usage(path: Path, *, usd: float | None, day: str = TODAY) -> None:
    append_usage(path, UsageRecord(ts=f"{day}T12:00:00+00:00", model="m", usd=usd))


# --- what the day cost -------------------------------------------------------------------------


def test_spent_today_counts_only_today(tmp_path: Path) -> None:
    log = tmp_path / "usage.jsonl"
    _usage(log, usd=1.0)
    _usage(log, usd=99.0, day="2020-01-01")

    assert spent_today(log, today=TODAY) == (1.0, False)


def test_an_unpriced_turn_makes_the_day_unknown_rather_than_cheap(tmp_path: Path) -> None:
    """The same rule as the per-run cap. Summing only the known part gives a total that is
    confidently too low, and too low in the direction that flatters whatever ran unpriced."""
    log = tmp_path / "usage.jsonl"
    _usage(log, usd=0.10)
    _usage(log, usd=None)

    spent, unpriced = spent_today(log, today=TODAY)

    assert spent == 0.10
    assert unpriced is True


def test_a_missing_log_is_zero_not_an_error(tmp_path: Path) -> None:
    # First run on a fresh machine. A cap that raised here would make the daemon refuse to start.
    assert spent_today(tmp_path / "nothing.jsonl", today=TODAY) == (0.0, False)


# --- the job's own ceiling ---------------------------------------------------------------------


def test_a_job_carries_its_own_cap_and_is_ordinary_by_default() -> None:
    plain = CronJob(id="a", name="a", schedule="* * * * *", action="x")
    assert plain.max_usd is None
    assert plain.critical is False, "a job must not exempt itself from the day's budget by default"

    capped = CronJob(id="b", name="b", schedule="* * * * *", action="x", max_usd=0.5, critical=True)
    assert capped.max_usd == 0.5
    assert capped.critical is True


# --- what a refusal looks like from outside ----------------------------------------------------


def test_a_refused_job_gets_its_own_status_not_error(tmp_path: Path) -> None:
    """`budget` and `error` need opposite responses: one is a number in the configuration, the other
    is a code fix. A refusal reported as an error sends whoever reads it to the wrong place."""
    sch = _scheduler(tmp_path)
    sch.schedule_cron("j", "* * * * *", "do X", now=0)

    def refuse(_job: CronJob) -> None:
        raise BudgetExceeded("daily cap reached: $5.0000 of $5.0000")

    ran = sch.run_due(now=120, dispatch=refuse)

    assert [j.last_status for j in ran] == ["budget"]
    assert "daily cap reached" in (ran[0].last_error or "")


def test_a_refusal_does_not_climb_the_failure_counter(tmp_path: Path) -> None:
    # Otherwise a spending decision reads as forty broken dispatches to anyone looking at
    # `failing()`, and the genuinely broken job hides behind it.
    sch = _scheduler(tmp_path)
    sch.schedule_cron("j", "* * * * *", "do X", now=0)

    def refuse(_job: CronJob) -> None:
        raise BudgetExceeded("daily cap reached")

    for minute in (120, 180, 240):
        sch.run_due(now=minute, dispatch=refuse)

    assert sch.store.list()[0].consecutive_failures == 0


def test_a_real_failure_still_counts(tmp_path: Path) -> None:
    # The counterpart: making budget refusals free must not make failures free too.
    sch = _scheduler(tmp_path)
    sch.schedule_cron("j", "* * * * *", "do X", now=0)

    def boom(_job: CronJob) -> None:
        raise RuntimeError("broken")

    # Two dispatches, each past its own boundary. Hardcoded 120/180 before schedules carried a
    # per-job offset, and then the second tick fell BEFORE the next due time — so only one dispatch
    # happened and the counter read 1, which looks like the counter being broken.
    for _ in range(2):
        sch.run_due(now=(sch.store.list()[0].next_run or 0) + 1, dispatch=boom)

    job = sch.store.list()[0]
    assert job.last_status == "error"
    assert job.consecutive_failures == 2


def test_the_schedule_still_advances_after_a_refusal(tmp_path: Path) -> None:
    """A refused job must not be due forever. If the cap holds all day, a schedule that never
    advanced would re-refuse on every tick and fill the log with the same line."""
    sch = _scheduler(tmp_path)
    job = sch.schedule_cron("j", "* * * * *", "do X", now=0)
    before = job.next_run

    sch.run_due(now=120, dispatch=lambda _j: (_ for _ in ()).throw(BudgetExceeded("nope")))

    assert (sch.store.list()[0].next_run or 0) > (before or 0)
