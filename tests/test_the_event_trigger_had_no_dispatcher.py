"""An event job could be created, was listed as enabled, and could never fire.

`Trigger` admits three values. `schedule_webhook` has a dispatcher — the webhook server calls
`fire_webhook`. `schedule_cron` has one — the daemon calls `run_due`. `schedule_event` had none:
`fire_event` was written, was covered by a unit test, and had no caller anywhere in `chimera/`.

So `chimera cron add --event deploy` accepted the job, `cron list` showed it enabled with a
schedule, and nothing ever ran it. The silence is identical to that of a job whose time has not
come, which is the worst kind: there is nothing to notice.

Two things were wrong and only one of them is the wiring. `fire_event` also swallowed the outcome —
a bare `try/except` around `dispatch(job)` with a log line, and no `_record`. So an event job's
failures never touched `last_status`, `last_error` or `consecutive_failures`: it would report as
healthy forever, and the failure brake that reads that counter would never see it.
"""

from __future__ import annotations

from pathlib import Path

from chimera.scheduler.engine import FAIL_LIMIT, Scheduler
from chimera.scheduler.store import CronStore


def _sched(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "jobs.json"))


def test_an_event_job_runs_when_its_event_fires(tmp_path: Path) -> None:
    sch = _sched(tmp_path)
    sch.schedule_event("pós-deploy", "deploy", "verifique o healthcheck")
    corridos: list[str] = []

    sch.fire_event("deploy", 100.0, lambda job: corridos.append(job.name))  # type: ignore[arg-type,func-returns-value]

    assert corridos == ["pós-deploy"]


def test_another_event_does_not_run_it(tmp_path: Path) -> None:
    """The guard against a dispatcher that fires everything: an event name that matches nothing is
    the ordinary case, and running the whole crontab for it would be worse than running none."""
    sch = _sched(tmp_path)
    sch.schedule_event("pós-deploy", "deploy", "verifique o healthcheck")
    corridos: list[str] = []

    sch.fire_event("git_push", 100.0, lambda job: corridos.append(job.name))  # type: ignore[arg-type,func-returns-value]

    assert corridos == []


# ------------------------------------------------------------------ the outcome it swallowed


def test_a_failed_event_job_says_it_failed(tmp_path: Path) -> None:
    """The second defect. Without this the job reports `ok` forever — the exact state the cron
    path's own comment says made a job that had failed every tick for a month look healthy."""
    sch = _sched(tmp_path)
    sch.schedule_event("pós-deploy", "deploy", "verifique o healthcheck")

    def explode(_job: object) -> None:
        raise RuntimeError("o healthcheck não respondeu")

    sch.fire_event("deploy", 100.0, explode)  # type: ignore[arg-type]
    job = sch.store.list()[0]

    assert job.last_status == "error"
    assert job.last_error and "healthcheck" in job.last_error
    assert job.consecutive_failures == 1


def test_a_successful_event_job_says_so(tmp_path: Path) -> None:
    sch = _sched(tmp_path)
    sch.schedule_event("pós-deploy", "deploy", "verifique o healthcheck")

    sch.fire_event("deploy", 100.0, lambda _job: None)  # type: ignore[arg-type]
    job = sch.store.list()[0]

    assert job.last_status == "ok"
    assert job.consecutive_failures == 0
    assert job.last_run == 100.0


def test_one_failing_event_job_does_not_stop_the_others(tmp_path: Path) -> None:
    """Same invariant the cron path holds: a failing job must not break the dispatcher."""
    sch = _sched(tmp_path)
    sch.schedule_event("primeiro", "deploy", "a")
    sch.schedule_event("segundo", "deploy", "b")
    vistos: list[str] = []

    def um_falha(job: object) -> None:
        vistos.append(job.name)  # type: ignore[attr-defined]
        if job.name == "primeiro":  # type: ignore[attr-defined]
            raise RuntimeError("falhou")

    sch.fire_event("deploy", 100.0, um_falha)  # type: ignore[arg-type]

    assert vistos == ["primeiro", "segundo"]


def test_an_event_job_that_only_fails_is_switched_off_too(tmp_path: Path) -> None:
    """The brake has to reach here, or the one trigger with no schedule to slow it down becomes the
    one that can hammer a provider without limit."""
    sch = _sched(tmp_path)
    sch.schedule_event("pós-deploy", "deploy", "verifique o healthcheck")

    def explode(_job: object) -> None:
        raise RuntimeError("falhou")

    for _ in range(FAIL_LIMIT):
        sch.fire_event("deploy", 100.0, explode)  # type: ignore[arg-type]

    assert sch.store.list()[0].enabled is False


def test_a_disabled_event_job_does_not_run(tmp_path: Path) -> None:
    """And the brake has to hold: `jobs_for_event` is what decides, so a job switched off by the
    brake must stop being selected rather than merely stop being reported."""
    sch = _sched(tmp_path)
    job = sch.schedule_event("pós-deploy", "deploy", "verifique o healthcheck")
    sch.disable(job.id)
    corridos: list[str] = []

    sch.fire_event("deploy", 100.0, lambda j: corridos.append(j.name))  # type: ignore[arg-type,func-returns-value]

    assert corridos == []
