"""`GET /api/cron` says who switched a disabled job off.

The engine has recorded it since the failure brake existed (`CronJob.disabled_by`), but the API
never sent it, so the desktop could only describe a braked job by its last error — and a client
reading `disabled_by` off the response was reading a field that was always missing.
"""

from __future__ import annotations

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


def test_a_braked_job_is_listed_as_braked_and_a_paused_one_as_paused(
    monkeypatch: Any, tmp_path: Any
) -> None:
    from chimera.config import get_settings
    from chimera.scheduler import CronJob, CronStore

    store = CronStore(tmp_path / "scheduler" / "jobs.json")
    store.add(
        CronJob(
            id="b", name="braked", schedule="0 9 * * *", action="x",
            enabled=False, disabled_by="brake", last_status="error", consecutive_failures=5,
        )
    )
    store.add(CronJob(id="r", name="running", schedule="0 9 * * *", action="x"))
    client = _client(monkeypatch, tmp_path)

    by_id = {j["id"]: j for j in client.get("/api/cron").json()}
    assert by_id["b"]["disabled_by"] == "brake"
    assert by_id["r"]["disabled_by"] == ""

    # Pausing by hand is the other value, and the routes that return one job carry it too.
    assert client.post("/api/cron/r/disable").json()["disabled_by"] == "human"
    assert client.post("/api/cron/r/enable").json()["disabled_by"] == ""
    get_settings.cache_clear()
