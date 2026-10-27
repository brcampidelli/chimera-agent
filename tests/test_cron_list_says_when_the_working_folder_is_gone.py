"""`GET /api/cron` says when a job names a working folder that is no longer on this computer.

The job "resumo do site" pointed at a folder that did not exist and still read "Ativa", the same as a
job that works. The schedule itself is not changed here: whether the folder comes back is not for the
engine to decide. The row only has to stop looking healthy.

A job with no folder is not flagged. It runs at the process root by design, and warning about that
would be a warning about a choice nobody made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from chimera.interface import ChatSession


class _NoAgent:
    """The cron routes never run the agent; the session only has to exist."""


def _client(monkeypatch: Any, tmp_path: Any) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(_NoAgent()), settings=Settings()))  # type: ignore[arg-type]  # never run: the cron routes do not call the agent


def test_a_job_whose_folder_is_gone_is_flagged_and_stays_enabled(
    monkeypatch: Any, tmp_path: Any
) -> None:
    from chimera.config import get_settings
    from chimera.scheduler import CronJob, CronStore

    here = tmp_path / "site"
    here.mkdir()
    store = CronStore(tmp_path / "scheduler" / "jobs.json")
    store.add(CronJob(id="gone", name="resumo do site", schedule="0 9 * * *", action="x",
                      workspace=str(tmp_path / "site-institucional")))
    store.add(CronJob(id="here", name="com pasta", schedule="0 9 * * *", action="x",
                      workspace=str(here)))
    store.add(CronJob(id="root", name="sem pasta", schedule="0 9 * * *", action="x"))
    client = _client(monkeypatch, tmp_path)

    by_id = {j["id"]: j for j in client.get("/api/cron").json()}

    assert by_id["gone"]["workspace_missing"] is True
    # The schedule is not switched off by this: it is reported, not decided.
    assert by_id["gone"]["enabled"] is True
    assert by_id["here"]["workspace_missing"] is False
    assert by_id["root"]["workspace_missing"] is False
    get_settings.cache_clear()


def test_the_single_job_route_reports_it_too(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings
    from chimera.scheduler import CronJob, CronStore

    store = CronStore(tmp_path / "scheduler" / "jobs.json")
    store.add(CronJob(id="gone", name="x", schedule="0 9 * * *", action="x",
                      workspace=str(Path(tmp_path) / "nope")))
    client = _client(monkeypatch, tmp_path)

    assert client.post("/api/cron/gone/disable").json()["workspace_missing"] is True
    get_settings.cache_clear()
