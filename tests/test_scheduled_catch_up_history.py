"""A scheduled time that passed while nothing ran: run it once, or say why it did not.

Before this, a job whose time came while the app was closed ran once on the next tick (by
accident of ``next_run <= now``) and said nothing about it, and a time that was skipped for any
other reason (the job was off, the day's money was spent) left no line anywhere: a schedule that
skips in silence reads exactly like a schedule with nothing due.

Held here, with the clock always a number the test passes in (the engine takes no clock):

* one run however many times were missed, for a missed time up to seven days old;
* nothing run for a missed time older than that, or under ``catch_up="skip"``;
* a history line per missed time, each with a reason from a closed vocabulary;
* a job written before the fields existed behaves as it always did.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler import Scheduler
from chimera.scheduler.engine import (
    CATCH_UP_GRACE_S,
    CATCH_UP_MAX_AGE_S,
    MAX_HISTORY_ROWS_PER_CATCH_UP,
)
from chimera.scheduler.history import REASONS, CronHistory, history_path
from chimera.scheduler.models import CronJob
from chimera.scheduler.store import CronStore

HOUR = 3600.0
DAY = 86400.0
#: An arbitrary instant. Nothing here reads the real clock.
T0 = float(datetime(2026, 3, 10, 12, 0, tzinfo=UTC).timestamp())


class _Calls:
    """A dispatch that counts."""

    def __init__(self) -> None:
        self.jobs: list[str] = []

    def __call__(self, job: CronJob) -> None:
        self.jobs.append(job.id)


def _sched(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), jitter=False)


def _history(sched: Scheduler, job_id: str) -> list[tuple[str, str]]:
    """``(outcome, reason)`` oldest first, so a test reads the story in order."""
    rows = CronHistory(history_path(sched.store.path.parent.parent)).load(job_id=job_id, limit=500)
    return [(r.outcome, r.reason) for r in reversed(rows)]


def _hourly(sched: Scheduler, **kw: object) -> CronJob:
    return sched.schedule_cron("hourly", "0 * * * *", "report", now=T0, **kw)  # type: ignore[arg-type]


def _monthly(sched: Scheduler, **kw: object) -> CronJob:
    return sched.schedule_cron("monthly", "0 7 1 * *", "report", now=T0, **kw)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- run once


def test_a_missed_time_runs_once_when_it_is_up_to_seven_days_old(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _monthly(sched)
    first = job.next_run or 0.0
    calls = _Calls()

    ran = sched.run_due(first + 5 * DAY, calls)

    assert calls.jobs == [job.id] and [j.id for j in ran] == [job.id]
    assert _history(sched, job.id) == [("ran", "catch_up")]
    # Resumed on the schedule: the next time is after now, not another catch-up tomorrow.
    assert (job.next_run or 0.0) > first + 5 * DAY
    assert job.last_run == first + 5 * DAY


def test_a_missed_time_older_than_seven_days_is_skipped_and_says_so(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _monthly(sched)
    first = job.next_run or 0.0
    calls = _Calls()

    now = first + CATCH_UP_MAX_AGE_S + HOUR
    ran = sched.run_due(now, calls)

    assert calls.jobs == [] and ran == []
    assert _history(sched, job.id) == [("skipped", "missed_too_old")]
    assert (job.next_run or 0.0) > now
    assert job.last_run is None, "a skipped time is not a run"


def test_the_limit_is_the_latest_missed_time_not_the_oldest(tmp_path: Path) -> None:
    """A daily job off for ten days has a missed time from yesterday: it runs once. Measured from
    the oldest missed time it would be skipped, which is the opposite of what 'catch up' means."""
    sched = _sched(tmp_path)
    job = sched.schedule_cron("daily", "0 7 * * *", "report", now=T0)
    first = job.next_run or 0.0
    calls = _Calls()

    sched.run_due(first + 10 * DAY + 2 * HOUR, calls)

    assert calls.jobs == [job.id]
    outcomes = _history(sched, job.id)
    assert outcomes[-1] == ("ran", "catch_up")


# --------------------------------------------------------------------------- never N times


def test_many_missed_times_are_one_run(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0
    calls = _Calls()

    sched.run_due(first + 5 * HOUR + 1800, calls)

    assert calls.jobs == [job.id], "six hours were missed and the job ran more than once"
    story = _history(sched, job.id)
    assert story.count(("ran", "catch_up")) == 1
    assert story.count(("skipped", "catch_up_merged")) == 5, story


def test_the_next_tick_does_not_run_the_job_again(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0
    calls = _Calls()

    now = first + 5 * HOUR + 1800
    sched.run_due(now, calls)
    sched.run_due(now + 30, calls)

    assert calls.jobs == [job.id]


# --------------------------------------------------------------------------- skip


def test_skip_does_not_recover_and_records_every_missed_time(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched, catch_up="skip")
    first = job.next_run or 0.0
    calls = _Calls()

    now = first + 5 * HOUR + 1800
    sched.run_due(now, calls)

    assert calls.jobs == []
    assert _history(sched, job.id) == [("skipped", "catch_up_skip")] * 6
    assert (job.next_run or 0.0) > now
    assert job.last_run is None


def test_skip_still_runs_a_time_that_is_merely_a_little_late(tmp_path: Path) -> None:
    """Behind a slow neighbour a job is minutes late with the scheduler alive. That is not a missed
    time, and under skip it must not be thrown away."""
    sched = _sched(tmp_path)
    job = _hourly(sched, catch_up="skip")
    first = job.next_run or 0.0
    calls = _Calls()

    sched.run_due(first + CATCH_UP_GRACE_S - 60, calls)

    assert calls.jobs == [job.id]
    assert _history(sched, job.id) == [("ran", "on_time")]


def test_a_long_outage_lists_the_recent_times_and_counts_the_rest(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched, catch_up="skip")
    first = job.next_run or 0.0

    sched.run_due(first + 100 * HOUR + 600, _Calls())

    rows = CronHistory(history_path(tmp_path)).load(job_id=job.id, limit=500)
    assert len(rows) == MAX_HISTORY_ROWS_PER_CATCH_UP + 1
    assert any("earlier missed time" in r.detail for r in rows)


# --------------------------------------------------------------------------- why it did not run


def test_a_switched_off_job_says_so_once(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0
    sched.disable(job.id)
    calls = _Calls()

    sched.run_due(first + 10, calls)
    sched.run_due(first + 20, calls)
    sched.run_due(first + HOUR + 10, calls)

    assert calls.jobs == []
    assert _history(sched, job.id) == [("skipped", "disabled")], "one line per outage, not per tick"
    # Switched back on, and off again: a new outage is worth a new line.
    sched.enable(job.id, now=first + 2 * HOUR)
    sched.disable(job.id)
    sched.run_due((job.next_run or 0.0) + 10, calls)
    assert _history(sched, job.id) == [("skipped", "disabled")] * 2


def test_a_run_refused_for_the_budget_is_a_skip_with_that_reason(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0

    def over_budget(_: CronJob) -> None:
        raise BudgetExceeded("daily cap reached")

    sched.run_due(first + 5, over_budget)

    assert _history(sched, job.id) == [("skipped", "budget_exceeded")]


def test_a_failed_run_is_recorded_with_its_status(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0

    def broken(_: CronJob) -> None:
        raise RuntimeError("boom")

    sched.run_due(first + 5, broken)

    rows = CronHistory(history_path(tmp_path)).load(job_id=job.id)
    assert [(r.outcome, r.reason, r.status) for r in rows] == [("ran", "error", "error")]
    assert "boom" in rows[0].detail


def test_a_job_still_dispatching_is_not_started_again(tmp_path: Path) -> None:
    """The schedule only advances after the dispatch ends, so a second tick on the same store while
    the first is inside the job would otherwise start it twice."""
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0
    inner_ran: list[list[str]] = []
    count = {"n": 0}

    def reentrant(j: CronJob) -> None:
        count["n"] += 1
        inner_ran.append([x.id for x in sched.run_due(first + 10, reentrant)])

    sched.run_due(first + 5, reentrant)

    assert count["n"] == 1 and inner_ran == [[]]
    assert ("skipped", "already_running") in _history(sched, job.id)


def test_every_reason_the_engine_writes_is_in_the_closed_set(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    a, b = _hourly(sched), _monthly(sched, catch_up="skip")
    first = (a.next_run or 0.0)
    sched.run_due(first + 5 * HOUR + 1800, _Calls())
    sched.run_due((b.next_run or 0.0) + 8 * DAY, _Calls())

    rows = CronHistory(history_path(tmp_path)).load(limit=500)
    assert rows and {r.reason for r in rows} <= set(REASONS)


# --------------------------------------------------------------------------- old jobs


def test_a_job_written_before_the_fields_existed_behaves_as_it_always_did(tmp_path: Path) -> None:
    path = tmp_path / "scheduler" / "jobs.json"
    path.parent.mkdir(parents=True)
    old = {
        "id": "old1", "name": "old", "trigger": "cron", "schedule": "0 * * * *",
        "action": "report", "enabled": True, "next_run": T0,
    }
    path.write_text(json.dumps([old]), encoding="utf-8")
    sched = Scheduler(CronStore(path), jitter=False)
    job = sched.store.get("old1")
    calls = _Calls()

    assert (job.catch_up, job.permission_mode, job.worktree) == ("once", "", False)
    sched.run_due(T0 + 3 * HOUR + 60, calls)

    assert calls.jobs == ["old1"], "the old behaviour is one run on the next tick"


def test_a_job_on_time_is_unchanged(tmp_path: Path) -> None:
    sched = _sched(tmp_path)
    job = _hourly(sched)
    first = job.next_run or 0.0
    calls = _Calls()

    assert sched.run_due(first - 1, calls) == []
    sched.run_due(first + 30, calls)

    assert calls.jobs == [job.id]
    assert _history(sched, job.id) == [("ran", "on_time")]


def test_a_permission_mode_that_is_not_a_mode_is_refused_when_the_job_is_written(
    tmp_path: Path,
) -> None:
    sched = _sched(tmp_path)

    with pytest.raises(ValueError):
        sched.schedule_cron("x", "0 * * * *", "report", now=T0, permission_mode="bypass")
    with pytest.raises(ValueError):
        sched.schedule_cron("x", "0 * * * *", "report", now=T0, permission_mode="anything")


def test_the_desktop_says_every_reason_in_words() -> None:
    """The screen builds `cron.history.reason.<reason>` from what the server writes, so a reason
    added here without a sentence there would render as a bare key in every language."""
    source = (Path(__file__).resolve().parent.parent / "apps/desktop/src/lib/i18n.tsx").read_text(
        encoding="utf-8"
    )
    missing = [r for r in REASONS if source.count(f'"cron.history.reason.{r}"') != 10]
    assert missing == []
