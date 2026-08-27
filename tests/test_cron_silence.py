"""Asking the schedule what it is not telling you.

`Scheduler.overdue` and `Scheduler.failing` were written, reasoned about carefully and tested, and
only `chimera cron doctor` in a terminal ever called them. The same shape as everything else in
this wave: built, correct, unreachable.

It matters more now that the app shows what schedules answered. A row with no results because the
job never fired and a row with no results because the job answered nothing look identical, and only
one of them is a problem. On the desktop the daemon IS the app, so "never fired" usually means the
window was closed when the job's time came — which nothing on screen said.

The two lists stay apart because the responses have nothing in common: one is fixed by opening the
app, the other by rewriting the action. Merging them into "problems" would hide that.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api.app import build_api_app
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    get_settings.cache_clear()
    return TestClient(
        build_api_app(  # type: ignore[arg-type]
            lambda: None, settings=Settings(CHIMERA_HOME=str(tmp_path / "home"))
        )
    )


def _store(tmp_path: Path) -> Any:
    from chimera.scheduler.store import CronStore

    return CronStore(tmp_path / "home" / "scheduler" / "jobs.json")


def _job(store: Any, **over: Any) -> Any:
    from chimera.scheduler.store import CronJob

    job = CronJob(
        id=over.pop("id", "j1"),
        name=over.pop("name", "resumo do site"),
        trigger="cron",
        schedule="0 7 * * *",
        action="liste os arquivos",
        enabled=over.pop("enabled", True),
        **over,
    )
    store.add(job)
    store.save()
    return job


def test_a_schedule_that_never_fired_is_reported(client: TestClient, tmp_path: Path) -> None:
    """The silence nothing else can see. `next_run` only moves when a job fires, so a time in the
    past is proof nothing dispatched."""
    _job(_store(tmp_path), next_run=time.time() - 3600)

    body = client.get("/api/cron/silence").json()

    assert [j["id"] for j in body["overdue"]] == ["j1"]
    assert body["overdue"][0]["behind_seconds"] > 3000
    assert body["failing"] == []


def test_a_tick_in_progress_is_not_a_miss(client: TestClient, tmp_path: Path) -> None:
    """The control, and it is what keeps the panel from crying wolf every thirty seconds. "Due four
    seconds ago" is the daemon getting to it, not a gap."""
    _job(_store(tmp_path), next_run=time.time() - 4)

    assert client.get("/api/cron/silence").json()["overdue"] == []


def test_the_threshold_is_reported_not_assumed(client: TestClient, tmp_path: Path) -> None:
    """A reader who cannot see the grace period cannot tell a real gap from the clock."""
    body = client.get("/api/cron/silence?grace_minutes=2").json()
    assert body["grace_seconds"] == 120.0


def test_a_job_that_runs_on_time_and_loses_is_the_other_list(
    client: TestClient, tmp_path: Path
) -> None:
    """The silence that looks healthiest: it IS running, `last_run` is recent, the schedule is
    advancing — and every dispatch has lost. Told apart because one says look at the daemon and the
    other says look at the job."""
    _job(
        _store(tmp_path),
        next_run=time.time() + 3600,
        consecutive_failures=3,
        last_status="error",
        last_error="HTTP 500",
    )

    body = client.get("/api/cron/silence").json()

    assert body["overdue"] == []
    assert body["failing"][0]["consecutive_failures"] == 3
    assert body["failing"][0]["last_error"] == "HTTP 500"


def test_a_disabled_job_is_neither(client: TestClient, tmp_path: Path) -> None:
    """Somebody turned it off. Reporting it as missed would report an instruction as a fault."""
    _job(_store(tmp_path), enabled=False, next_run=time.time() - 9999, consecutive_failures=4)

    body = client.get("/api/cron/silence").json()

    assert body["overdue"] == [] and body["failing"] == []


def test_a_healthy_schedule_says_nothing(client: TestClient, tmp_path: Path) -> None:
    _job(_store(tmp_path), next_run=time.time() + 3600)

    body = client.get("/api/cron/silence").json()

    assert body["overdue"] == [] and body["failing"] == []


def test_no_schedules_at_all_is_not_an_error(client: TestClient) -> None:
    body = client.get("/api/cron/silence").json()
    assert body == {"overdue": [], "failing": [], "grace_seconds": 300.0}


def test_the_path_is_not_taken_for_a_job_id(client: TestClient, tmp_path: Path) -> None:
    """FastAPI matches in declaration order, and `/api/cron/{job_id}` would otherwise swallow this
    — answering 404 for a path that exists, which is the kind of failure nobody thinks to check."""
    r = client.get("/api/cron/silence")
    assert r.status_code == 200
    assert "overdue" in r.json()
