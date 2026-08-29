"""The gate a scheduled job can opt into, and the three layers that all had to carry it.

`CronJob.verify` and `CronJob.max_attempts` shipped with the scheduled run's harness, and the
dispatch arms the verify-or-revert loop only when `verify` is non-empty. Nothing could write either
one — not `POST /api/cron`, not `chimera cron add`, not the Automation screen — so for every user
`verify` was permanently `""`, the gate never armed, and that change kept its accounting half and
none of the rest.

Found by installing the release and trying to schedule a job that edits code. The frontend half is
held by `apps/desktop/src/components/Cron.verify.test.tsx`; this file holds the two layers below it,
because a field that reaches the route and stops there is the same defect one floor down.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.scheduler import Scheduler
from chimera.scheduler.store import CronStore


def _client(tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app
    from chimera.interface import ChatSession
    from tests.test_api import _FakeAgent  # the suite's existing stand-in

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))


def _store(tmp_path: Path) -> CronStore:
    return CronStore(tmp_path / "jobs.json")


# --------------------------------------------------------------------------- the scheduler


def test_every_trigger_can_carry_the_gate(tmp_path: Path) -> None:
    """Cron, event and webhook. A webhook job is the one most likely to edit code on somebody
    else's schedule, so leaving it out would exempt the riskiest of the three."""
    sched = Scheduler(_store(tmp_path))

    cron = sched.schedule_cron("a", "0 7 * * *", "fix it", now=0.0, verify="pytest -q")
    event = sched.schedule_event("b", "deploy", "fix it", verify="pytest -q")
    hook = sched.schedule_webhook("c", "push", "fix it", verify="pytest -q")

    assert [j.verify for j in (cron, event, hook)] == ["pytest -q"] * 3


def test_a_job_without_a_gate_is_unchanged(tmp_path: Path) -> None:
    """Opt-in, and the default has to stay exactly where it was: most scheduled jobs are reports
    that change no files, and the diff gate fails an attempt that changed none."""
    job = Scheduler(_store(tmp_path)).schedule_cron("a", "0 7 * * *", "summarise", now=0.0)

    assert job.verify == "" and job.max_attempts == 1


def test_max_attempts_can_never_be_zero(tmp_path: Path) -> None:
    """Zero attempts is a job that cannot run. Clamped rather than rejected because it arrives from
    a form field, and refusing a schedule over a number nobody meant is worse than fixing it."""
    job = Scheduler(_store(tmp_path)).schedule_cron("a", "0 7 * * *", "x", now=0.0, max_attempts=0)

    assert job.max_attempts == 1


# --------------------------------------------------------------------------- the HTTP route


def test_the_route_stores_the_gate_and_reads_it_back(tmp_path: Path) -> None:
    """The layer that was missing. The screen sends it; without this the value was accepted into a
    pydantic model and dropped on the floor before it reached the scheduler."""
    client = _client(tmp_path)

    created = client.post(
        "/api/cron",
        json={
            "name": "nightly fix",
            "schedule": "0 3 * * *",
            "action": "fix the failing test",
            "verify": "pytest -q",
            "max_attempts": 2,
        },
    ).json()

    assert created["verify"] == "pytest -q"
    assert created["max_attempts"] == 2
    listed = client.get("/api/cron").json()[0]
    assert listed["verify"] == "pytest -q", "the gate did not survive the round trip"


def test_a_client_that_sends_no_gate_keeps_the_old_behaviour(tmp_path: Path) -> None:
    """An older client, or a report job. Neither may start failing because a field appeared."""
    client = _client(tmp_path)

    created = client.post(
        "/api/cron", json={"name": "brief", "schedule": "0 7 * * *", "action": "summarise"}
    ).json()

    assert created["verify"] == "" and created["max_attempts"] == 1


def test_a_job_written_before_these_fields_existed_still_reads(tmp_path: Path) -> None:
    """The store holds jobs from earlier versions. Reading the row must not raise on their absence
    — a screen that 500s on old data is a worse outcome than the gate not existing."""
    from chimera.api.features import _job_dict

    class _Old:
        id, name, trigger, schedule, action = "j1", "old", "cron", "0 7 * * *", "x"
        enabled, next_run, last_run = True, None, None
        last_status = last_error = None
        consecutive_failures, created_by = 0, "human"
        workspace = deliver_to = None

    row: dict[str, Any] = _job_dict(_Old())

    assert row["verify"] == "" and row["max_attempts"] == 1


# --------------------------------------------------------------------------- the CLI


def test_the_cli_can_arm_the_gate(tmp_path: Path, monkeypatch: Any) -> None:
    """`chimera cron add --verify`. The CLI is where a job that edits code is most likely to be
    written, and it had no way to say what would prove the work."""
    from typer.testing import CliRunner

    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["cron", "add", "nightly", "0 3 * * *", "fix the test",
         "--verify", "pytest -q", "--max-attempts", "3"],
    )

    assert result.exit_code == 0, result.output
    store = CronStore(tmp_path / "home" / "scheduler" / "jobs.json")
    jobs = store.list()
    assert jobs, f"no job was stored: {result.output}"
    assert jobs[0].verify == "pytest -q"
    assert jobs[0].max_attempts == 3
