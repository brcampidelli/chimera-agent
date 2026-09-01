"""The two silences a schedule can produce, and telling them apart.

Every honesty mechanism in this project sits downstream of a run having happened: the verifier
judges a result, the diff gate measures a change, the receipt names who approved it. None of them
gets a turn when the run never occurred — and a schedule that produces no receipt reads as a
schedule with nothing due.

There are two of those silences and they look identical from `last_run`:

  * **Nothing ran.** The daemon is down, the container was not restarted, the machine slept. No
    exception, no receipt, no verdict, not even an `unknown`.
  * **Everything ran and lost.** The daemon is alive, `last_run` is a minute ago, the schedule is
    advancing, and every dispatch has failed for a month. This one looks *healthier* than the first.

`last_run` cannot distinguish them, because the scheduler sets it OUTSIDE the try/except on purpose
— a failing job must not stall the tick. So the outcome is recorded beside it, and each silence has
its own question: `overdue()` and `failing()`.

Credit: the framing is u/MediaPositive4282's, in the thread quoted in issue #26 — "a schedule that
produces no receipt invites the reader to assume nothing was due".
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("croniter")

from chimera.scheduler.engine import FAIL_LIMIT, Scheduler  # noqa: E402
from chimera.scheduler.models import CronJob  # noqa: E402
from chimera.scheduler.store import CronStore  # noqa: E402

HOUR = 3600.0
DAY = 24 * HOUR


def _scheduler(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "cron.json"))


def _hourly(sched: Scheduler, name: str, now: float) -> CronJob:
    return sched.schedule_cron(name, "0 * * * *", f"do {name}", now=now)


def _cli_scheduler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Scheduler:
    """A scheduler pointed at the file the CLI will actually open.

    Two things have to line up or the command reads an empty store and every assertion below
    passes for the wrong reason: the path is `home/scheduler/jobs.json`, not a name of my choosing,
    and `get_settings()` is cached — setting the env var after the first read changes nothing.
    """
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    get_settings.cache_clear()
    return Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"))


# --------------------------------------------------------------- the outcome, beside the attempt


def test_a_successful_dispatch_records_that_it_won(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    sched.run_due(job.next_run or 0.0, lambda _job: None)

    fresh = sched.store.get(job.id)
    assert fresh.last_status == "ok"
    assert fresh.last_error is None
    assert fresh.consecutive_failures == 0


def test_a_failing_dispatch_still_advances_the_schedule_and_now_says_it_failed(
    tmp_path: Path,
) -> None:
    """The behaviour that was already right, plus the fact that was missing.

    Advancing the schedule on failure is deliberate — a broken job must not stall every other one.
    What made it dishonest is that `last_run` was the only record, so the job looked like it had
    just worked.
    """
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    when = job.next_run or 0.0

    def explode(_job: CronJob) -> None:
        raise RuntimeError("o provedor caiu")

    sched.run_due(when, explode)
    fresh = sched.store.get(job.id)

    assert fresh.last_run == when, "the tick must still move on"
    assert fresh.next_run is not None and fresh.next_run > when
    assert fresh.last_status == "error"
    assert "o provedor caiu" in (fresh.last_error or "")
    assert fresh.consecutive_failures == 1


def test_an_abandoned_job_is_a_timeout_not_an_error(tmp_path: Path) -> None:
    """Different cause, different fix: a hung provider call is not a raising job."""
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "slow", 0.0)

    def hang(_job: CronJob) -> None:
        import time

        time.sleep(5)

    sched.run_due(job.next_run or 0.0, hang, job_timeout=0.05)
    fresh = sched.store.get(job.id)
    assert fresh.last_status == "timeout"
    assert "abandoned" in (fresh.last_error or "")


def test_failures_accumulate_and_a_success_clears_them(tmp_path: Path) -> None:
    """One failure is weather; forty is a broken job, and one `last_status` cannot tell them apart."""
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    falha = True

    def flaky(_job: CronJob) -> None:
        if falha:
            raise RuntimeError("x")

    for _ in range(3):
        sched.run_due(sched.store.get(job.id).next_run or 0.0, flaky)
    assert sched.store.get(job.id).consecutive_failures == 3

    falha = False
    sched.run_due(sched.store.get(job.id).next_run or 0.0, flaky)
    fresh = sched.store.get(job.id)
    assert fresh.consecutive_failures == 0
    assert fresh.last_status == "ok"
    assert fresh.last_error is None


# --------------------------------------------------------------------------- silence one: nothing ran


def test_a_job_nobody_dispatched_is_overdue_by_how_long(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    tres_dias_depois = (job.next_run or 0.0) + 3 * DAY

    atrasados = sched.overdue(tres_dias_depois, grace=HOUR)
    assert len(atrasados) == 1
    atrasado, quanto = atrasados[0]
    assert atrasado.id == job.id
    assert quanto == pytest.approx(3 * DAY, abs=1)


def test_a_tick_in_progress_is_not_a_miss(tmp_path: Path) -> None:
    """`grace` is why this is usable: due a second ago is the daemon working, not the daemon gone."""
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    assert sched.overdue((job.next_run or 0.0) + 30, grace=HOUR) == []


def test_a_disabled_job_is_not_overdue(tmp_path: Path) -> None:
    """Nothing was due. Reporting it would train the reader to ignore the report."""
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    sched.disable(job.id)
    assert sched.overdue((job.next_run or 0.0) + 3 * DAY, grace=HOUR) == []


def test_an_event_job_is_never_overdue(tmp_path: Path) -> None:
    """It has no next_run. A job waiting for an event that has not happened is not late."""
    sched = _scheduler(tmp_path)
    sched.schedule_event("on-push", "push", "do it")
    assert sched.overdue(9e9, grace=HOUR) == []


def test_the_latest_comes_first(tmp_path: Path) -> None:
    """Sorted by how long it has been missing, worst first.

    The hourly job's slot came round at hour 1 and the daily one's at hour 24, so with nothing
    running since, the hourly job has been unrun the longest — even though it is the one that
    "should" have run most recently. The first version of this assertion had the order backwards
    for exactly that reason, which is the confusion the sort exists to settle.
    """
    sched = _scheduler(tmp_path)
    a = _hourly(sched, "a", 0.0)
    b = sched.schedule_cron("b", "0 0 * * *", "do b", now=0.0)
    quando = max(a.next_run or 0.0, b.next_run or 0.0) + 10 * DAY

    atrasados = sched.overdue(quando, grace=HOUR)
    assert [job.name for job, _ in atrasados] == ["a", "b"]
    assert atrasados[0][1] > atrasados[1][1]


# ------------------------------------------------------- silence two: it ran, and lost, every time


def test_a_job_that_ran_and_failed_every_time_is_not_overdue_but_is_failing(tmp_path: Path) -> None:
    """The pair that is the whole point.

    This job is dispatched on time and has never once succeeded. `overdue` says nothing — correctly,
    the schedule is being honoured — and it is exactly the job somebody needs to hear about. The two
    questions do not overlap and neither one alone is enough.

    Written as "forever" and asserting twenty-four straight failures. That world no longer exists:
    the failure brake switches a job off at `FAIL_LIMIT`, so the fifth dispatch is the last one. The
    point survives the change and gets sharper — a braked job is STILL in `failing()`, and that is
    the exception `disabled_by` exists for. Filtering the report on `enabled` alone would have made
    the brake hide its own findings in the one place someone looks for them.
    """
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)

    def explode(_job: CronJob) -> None:
        raise RuntimeError("todo dia")

    agora = job.next_run or 0.0
    for _ in range(24):
        agora = sched.store.get(job.id).next_run or agora
        sched.run_due(agora, explode)

    parado = sched.store.get(job.id)
    assert sched.overdue(agora + 60, grace=HOUR) == [], "the schedule was honoured"
    assert [j.id for j in sched.failing(at_least=5)] == [job.id], "a braked job is still reported"
    assert parado.consecutive_failures == FAIL_LIMIT
    assert parado.enabled is False and parado.disabled_by == "brake"


def test_a_healthy_job_is_in_neither_list(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path)
    job = _hourly(sched, "brief", 0.0)
    agora = job.next_run or 0.0
    sched.run_due(agora, lambda _job: None)

    assert sched.overdue(agora + 60, grace=HOUR) == []
    assert sched.failing() == []


# ------------------------------------------------------------------------------ the surfaces


def test_cron_list_says_when_jobs_are_failing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`enabled` and `schedule` together read as health and say nothing about the last dispatch.

    A line rather than a column: the table was already at its width budget with six, and adding a
    seventh truncated the name — which broke an existing test and would have made the warning the
    easiest thing on screen to scroll past.
    """
    from typer.testing import CliRunner

    from chimera.cli.main import app

    sched = _cli_scheduler(tmp_path, monkeypatch)
    job = _hourly(sched, "brief", 0.0)

    def explode(_job: CronJob) -> None:
        raise RuntimeError("x")

    for _ in range(3):
        sched.run_due(sched.store.get(job.id).next_run or 0.0, explode)

    out = CliRunner().invoke(app, ["cron", "list"]).output
    assert "failing" in out
    assert "3 in a row" in out


def test_cron_doctor_separates_the_two_silences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The command exists to give different advice for the two, because the fixes differ."""
    from typer.testing import CliRunner

    from chimera.cli.main import app

    sched = _cli_scheduler(tmp_path, monkeypatch)
    # One that ran and lost, one that was never dispatched at all.
    perdedor = _hourly(sched, "perdedor", 0.0)
    sched.run_due(perdedor.next_run or 0.0, lambda _job: (_ for _ in ()).throw(RuntimeError("x")))
    sched.schedule_cron("esquecido", "0 * * * *", "do it", now=0.0)

    out = CliRunner().invoke(app, ["cron", "doctor"]).output
    assert "never dispatched" in out
    assert "about the daemon" in out
    assert "failing" in out
    assert "about the jobs" in out


def test_cron_doctor_says_what_it_cannot_see(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A green report that does not state its blind spot is the silence this issue is about."""
    from typer.testing import CliRunner

    from chimera.cli.main import app

    _cli_scheduler(tmp_path, monkeypatch)

    out = CliRunner().invoke(app, ["cron", "doctor"]).output
    assert "on schedule" in out
    assert "nothing here watches while this process is not running" in out
