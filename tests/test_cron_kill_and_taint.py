"""The operator's stop switch: `cron kill` halts a running dispatch, and nothing else.

The Hard Stop study documented a VPS invaded for 4.5 days by an ungoverned agent; the heartbeat
(PR #669) sees a dead daemon but nothing stopped a live one gone wrong. This pins the kill path:
a flag file the CLI writes and the dispatch consumes, a `cancelled` outcome that is neither a
failure (no ride into the brake) nor a success (no reset over real failures), and a flag that
never outlives the run it stopped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from chimera.config import Settings
from chimera.scheduler import CronStore, Scheduler
from chimera.scheduler.job_runner import make_run_job
from chimera.scheduler.models import CronJob, kill_flag_path


def _scheduler(tmp_path: Path) -> Scheduler:
    return Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"))


# --- the engine: the request, and what it must not do --------------------------------------------


def test_kill_writes_the_flag_and_refuses_a_disabled_job(tmp_path: Path) -> None:
    sch = _scheduler(tmp_path)
    vivo = sch.schedule_cron("vivo", "* * * * *", "do X", now=0)
    morto = sch.schedule_cron("morto", "* * * * *", "do X", now=0)
    sch.disable(morto.id)

    assert sch.kill(vivo.id, now=100) is True
    assert kill_flag_path(tmp_path, vivo.id).exists()
    # A disabled job has nothing running to stop; a flag written for it would be a lie the next
    # `enable` would spend.
    assert sch.kill(morto.id, now=100) is False
    assert not kill_flag_path(tmp_path, morto.id).exists()
    assert sch.kill("fantasma", now=100) is False


def test_a_kill_does_not_climb_or_reset_the_failure_counter(tmp_path: Path) -> None:
    """Neither reading is true: the job ran and was stopped by a person."""
    sch = _scheduler(tmp_path)
    job = sch.schedule_cron("j", "* * * * *", "do X", now=0)
    job.consecutive_failures = 2
    sch.store.add(job)

    ran = sch.run_due(now=(job.next_run or 0) + 1, dispatch=lambda _j: "cancelled")

    assert [j.last_status for j in ran] == ["cancelled"]
    assert ran[0].consecutive_failures == 2, "a kill must not read as health or as breakage"


def test_a_kill_pushes_the_next_run_past_the_request(tmp_path: Path) -> None:
    """A kill aimed at a job whose minute has not come must not fire it anyway."""
    sch = _scheduler(tmp_path)
    job = sch.schedule_cron("j", "* * * * *", "do X", now=0)
    antes = job.next_run

    sch.kill(job.id, now=100)

    depois = sch.store.list()[0].next_run
    assert depois is not None and antes is not None and depois > antes


# --- the dispatch: the flag is consumed, the run is cancelled ------------------------------------


class _Result:
    def __init__(self, content: str) -> None:
        self.content = content
        self.model = "fake/model"
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Backend:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        return _Result("pronto")


def _run_job(tmp_path: Path, backend: _Backend) -> Any:
    home = tmp_path / "home"
    projeto = tmp_path / "projeto"
    projeto.mkdir(parents=True, exist_ok=True)
    settings = Settings(**{"CHIMERA_HOME": str(home)})
    return make_run_job(
        settings=settings,
        backend=backend,
        workspace=tmp_path / "outra",
        model="fake/model",
        max_steps=3,
        usage_path=home / "usage.jsonl",
    ), home


def _plant_flag(home: Path, job_id: str) -> Path:
    """Write the kill flag the way `Scheduler.kill` does — folder first."""
    flag = kill_flag_path(home, job_id)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("kill", encoding="utf-8")
    return flag


def _job(tmp_path: Path, **overrides: Any) -> CronJob:
    fields: dict[str, Any] = dict(
        id="j1", name="nightly", trigger="cron", schedule="* * * * *",
        action="escreva algo", workspace=str(tmp_path / "projeto"),
    )
    fields.update(overrides)
    return CronJob(**fields)


def test_a_flag_present_at_dispatch_stops_the_run_before_any_model_call(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    backend = _Backend()
    run_job, home = _run_job(tmp_path, backend)
    _plant_flag(home, "j1")

    outcome = run_job(_job(tmp_path))

    assert outcome.cancelled is True
    assert backend.calls == 0, "a killed dispatch paid for a model call"
    assert not kill_flag_path(home, "j1").exists(), "the flag outlived the run it stopped"


def test_a_kill_that_arrives_mid_run_ends_the_run_cancelled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The flag appears during attempt 1, which FAILS its gate; the loop halts at the top of
    attempt 2 instead of paying for it. A kill during a run that would have succeeded anyway is
    not a kill — the run finished first, and the answer stands."""
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    backend = _Backend()
    run_job, home = _run_job(tmp_path, backend)
    flag = kill_flag_path(home, "j1")
    flag.parent.mkdir(parents=True, exist_ok=True)

    def plant_after_first_call(messages: list[Any], **kwargs: Any) -> _Result:
        backend.calls += 1
        if backend.calls == 1:
            flag.write_text("kill", encoding="utf-8")
        return _Result("pronto")

    backend.complete = plant_after_first_call  # type: ignore[method-assign]

    outcome = run_job(_job(
        tmp_path,
        verify=f'"{sys.executable}" -c "import sys; sys.exit(1)"',
        max_attempts=2,
    ))

    assert outcome.cancelled is True
    assert backend.calls == 1, "the second attempt must never have started"
    assert not flag.exists()


def test_no_flag_leaves_the_dispatch_alone(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("CHIMERA_HOST_EXEC", "allow")
    backend = _Backend()
    run_job, home = _run_job(tmp_path, backend)

    outcome = run_job(_job(tmp_path))

    assert outcome.cancelled is False
    assert backend.calls > 0
    assert not kill_flag_path(home, "j1").exists()
