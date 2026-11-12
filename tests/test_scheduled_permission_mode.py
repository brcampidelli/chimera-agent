"""The permission mode of a scheduled task: never wider than Settings, never asking, never the bridge's.

A scheduled run has nobody watching, so the mode is a ceiling:

* a mode wider than the install's own posture is refused where the owner saves it, and narrowed again
  at run time if Settings were tightened afterwards;
* ``bypass`` is not a mode a job can hold;
* a mode that would ask a person (manual, accept edits) removes the tools that would ask, so the run
  neither waits nor answers for itself;
* the bridge neither sets nor changes it, at any tier.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api.bridge_routes import FULL_CONTROL_NEVER, OWNER_DECISION_ROUTES, ROUTES
from chimera.api.posture import STORABLE_MODES
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun
from chimera.scheduler.job_permission import refusal_for, resolve
from chimera.scheduler.job_runner import make_run_job
from chimera.scheduler.models import JOB_PERMISSION_MODES, CronJob


def _settings(tmp_path: Path, **env: str) -> Settings:
    return Settings(**{"CHIMERA_HOME": str(tmp_path / "home"), **env})  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the rule


def test_the_modes_a_job_may_hold_are_the_code_screens_without_bypass() -> None:
    assert set(JOB_PERMISSION_MODES) == STORABLE_MODES


def test_a_mode_wider_than_settings_is_refused_with_the_reason(tmp_path: Path) -> None:
    auto = _settings(tmp_path, CHIMERA_APPROVAL="suspicious", CHIMERA_REACH="workspace")

    assert refusal_for("dont_ask", auto) is not None
    assert "never wider" in (refusal_for("dont_ask", auto) or "")
    assert refusal_for("manual", auto) is None
    assert refusal_for("auto", auto) is None
    assert refusal_for("", auto) is None


def test_a_job_never_holds_bypass(tmp_path: Path) -> None:
    permissive = _settings(tmp_path, CHIMERA_APPROVAL="never", CHIMERA_REACH="workspace_shell")

    assert refusal_for("bypass", permissive) is not None
    with pytest.raises(ValueError):
        resolve("bypass", permissive)
    with pytest.raises(ValueError):
        resolve("nonsense", permissive)


def test_a_mode_tightened_settings_made_too_wide_runs_narrower(tmp_path: Path) -> None:
    """Saved under permissive Settings, run after the owner tightened them."""
    tight = _settings(tmp_path, CHIMERA_APPROVAL="always", CHIMERA_REACH="read_only")

    permission = resolve("dont_ask", tight)

    assert permission is not None and permission.narrowed
    assert {"write_file", "run_shell"} <= set(permission.deny)


def test_manual_and_accept_edits_refuse_what_they_would_ask_about(tmp_path: Path) -> None:
    always = _settings(tmp_path, CHIMERA_APPROVAL="never", CHIMERA_REACH="workspace_shell")

    manual = resolve("manual", always)
    accept = resolve("accept_edits", always)

    assert manual is not None and {"write_file", "edit_file", "run_shell"} <= set(manual.deny)
    assert accept is not None and "run_shell" in accept.deny and "write_file" not in accept.deny


def test_plan_reads_and_writes_nothing(tmp_path: Path) -> None:
    permission = resolve("plan", _settings(tmp_path, CHIMERA_REACH="workspace_shell"))

    assert permission is not None and {"write_file", "run_shell"} <= set(permission.deny)


def test_no_mode_means_no_change() -> None:
    assert resolve("", cast(Settings, object())) is None


# --------------------------------------------------------------------------- the run


class _Result:
    def __init__(self, content: str, calls: list[Any] | None = None) -> None:
        self.content = content
        self.model = "fake/model"
        self.prompt_tokens = 10
        self.completion_tokens = 5
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls = calls or []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Writer:
    """Asks to write a file on step one, answers on step two, and keeps what it was offered."""

    def __init__(self) -> None:
        self.calls = 0
        self.offered: list[str] = []
        self.observed: list[str] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        self.offered.append(json.dumps(kwargs.get("tools") or []))
        if self.calls == 1:
            from chimera.providers.gateway import ToolCall

            return _Result(
                "", [ToolCall(id="c1", name="write_file",
                              arguments={"path": "out.txt", "content": "hello"})]
            )
        self.observed.append(json.dumps(messages, default=str))
        return _Result("done")


def _run(tmp_path: Path, mode: str, **env: str) -> tuple[Path, _Writer]:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(tmp_path, **env)
    backend = _Writer()
    run_job = make_run_job(
        settings=settings, backend=backend, workspace=project, model="fake/model", max_steps=4,
        usage_path=tmp_path / "home" / "usage.jsonl",
    )
    run_job(CronJob(id="j", name="n", schedule="* * * * *", action="write", workspace=str(project),
                    permission_mode=mode))
    return project, backend


def test_manual_with_no_screen_refuses_the_edit_and_does_not_wait(tmp_path: Path) -> None:
    project, backend = _run(tmp_path, "manual", CHIMERA_APPROVAL="suspicious")

    assert not (project / "out.txt").exists(), "an edit that would have been a question was made"
    assert '"write_file"' not in backend.offered[0], "the tool that would ask was still offered"
    assert backend.calls == 2, "the run ended; it did not hang on a question"


def test_a_job_with_no_mode_still_writes(tmp_path: Path) -> None:
    """The control: the refusal above comes from the mode, not from the harness."""
    project, _ = _run(tmp_path, "")

    assert (project / "out.txt").read_text(encoding="utf-8") == "hello"


def test_a_hand_written_bypass_makes_the_run_fail_loudly(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _run(tmp_path, "bypass")


# --------------------------------------------------------------------------- the routes


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    from tests.test_api import _FakeAgent

    app = build_api_app(lambda: ChatSession(cast(SupportsRun, _FakeAgent())))
    return TestClient(app)


def _create(client: TestClient, **extra: Any) -> Any:
    return client.post(
        "/api/cron", json={"name": "n", "schedule": "0 7 * * *", "action": "report", **extra}
    )


def test_the_screen_may_save_an_equal_or_stricter_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, CHIMERA_APPROVAL="suspicious")

    made = _create(client, permission_mode="manual", catch_up="skip", worktree=True)

    assert made.status_code == 200, made.text
    body = made.json()
    assert (body["permission_mode"], body["catch_up"], body["worktree"]) == ("manual", "skip", True)
    changed = client.put(f"/api/cron/{body['id']}/permission-mode", json={"permission_mode": "plan"})
    assert changed.status_code == 200 and changed.json()["permission_mode"] == "plan"
    cleared = client.put(f"/api/cron/{body['id']}/permission-mode", json={"permission_mode": ""})
    assert cleared.json()["permission_mode"] == ""


def test_the_screen_cannot_save_a_wider_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, CHIMERA_APPROVAL="suspicious")

    refused = _create(client, permission_mode="dont_ask")
    plain = _create(client).json()
    refused_put = client.put(f"/api/cron/{plain['id']}/permission-mode",
                             json={"permission_mode": "dont_ask"})
    bypass = _create(client, permission_mode="bypass")

    assert refused.status_code == 400 and "never wider" in refused.json()["detail"]
    assert refused_put.status_code == 400
    assert bypass.status_code == 422
    assert [j["permission_mode"] for j in client.get("/api/cron").json()] == [""]


def test_an_old_job_lists_with_the_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    scheduler_dir = tmp_path / "home" / "scheduler"
    scheduler_dir.mkdir(parents=True)
    (scheduler_dir / "jobs.json").write_text(
        json.dumps([{"id": "old", "name": "o", "schedule": "0 7 * * *", "action": "a"}]),
        encoding="utf-8",
    )

    row = client.get("/api/cron").json()[0]

    assert (row["catch_up"], row["permission_mode"], row["worktree"]) == ("once", "", False)
    assert client.get("/api/cron/old/history").json() == []
    assert client.get("/api/cron/nope/history").status_code == 404


# --------------------------------------------------------------------------- the bridge


URL = "http://127.0.0.1:65009"


def _bridge_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full_control: bool) -> Any:
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL_CONTROL", "true" if full_control else "false")
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def test_the_mode_route_is_the_owners_at_every_tier() -> None:
    assert "cron.permission_mode" in OWNER_DECISION_ROUTES
    assert "cron.permission_mode" in FULL_CONTROL_NEVER
    assert "cron.permission_mode" not in ROUTES


@pytest.mark.parametrize("full_control", [False, True])
def test_the_bridge_does_not_change_a_mode(
    full_control: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _bridge_app(tmp_path, monkeypatch, full_control=full_control)
    (tmp_path / "other").mkdir()
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    with TestClient(app) as client:
        direct = client.post(
            "/api/bridge/call",
            json={"route": "cron.permission_mode", "params": {"job_id": "j"},
                  "body": {"permission_mode": "plan"}},
            headers=headers,
        )
        created = client.post(
            "/api/bridge/call",
            json={"route": "cron.create",
                  "body": {"name": "n", "schedule": "0 7 * * *", "action": "a",
                           "permission_mode": "manual", "workspace": str(tmp_path / "other")}},
            headers=headers,
        )
        jobs = client.get("/api/cron").json()

    assert direct.status_code == 403 and created.status_code == 403
    assert created.json()["detail"] == OWNER_DECISION_ROUTES["cron.permission_mode"]
    assert jobs == [], "a task was written through the bridge with a mode the client chose"
