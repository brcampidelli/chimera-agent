"""Security regressions for the owner's screen-only permission mode."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


def test_queue_keeps_screen_provenance_only_in_process_memory(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue(
        "session-1", "request-1", {"message": "desktop_send"},
        source="bridge", via_screen=False,
    )
    reopened = CodeTurnQueue(tmp_path).get("session-1", item.id)
    assert reopened is not None
    assert reopened.source == "bridge"
    assert reopened.via_screen is False


def test_screen_queue_provenance_is_lost_after_restart(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    item, _ = CodeTurnQueue(tmp_path).enqueue(
        "session-1", "request-1", {"message": "approve"},
        source="app", via_screen=True,
    )
    reopened = CodeTurnQueue(tmp_path).get("session-1", item.id)
    assert reopened is not None and reopened.via_screen is True
    # A fresh process has no in-memory authority, regardless of what the old file claims.
    import chimera.api.code_session_queue as queue_module

    queue_module._SCREEN_QUEUE_IDS.clear()
    restarted = CodeTurnQueue(tmp_path).get("session-1", item.id)
    assert restarted is not None and restarted.via_screen is False


def test_bypass_audit_keeps_full_action_tool_and_run_id(tmp_path: Path) -> None:
    from chimera.governance.audit import AuditLog
    from chimera.governance.ledger_tool import LedgeredTool

    audit = AuditLog(tmp_path / "audit.jsonl")
    wrapped = LedgeredTool.__new__(LedgeredTool)
    wrapped.audit = audit
    wrapped.notify = None
    wrapped.run_id = "run-123"
    action = "x" * 1200
    wrapped._record_bypass(action)
    record = audit.entries()[-1]
    assert record["type"] == "bypass_permissions"
    assert record["action"] == action
    assert record["tool"]
    assert record["run_id"] == "run-123"


def test_turn_without_permission_field_does_not_clear_stored_mode() -> None:
    from chimera.api.code_api import CodeTurnRequest

    request = CodeTurnRequest(message="continue")
    assert "bypass_permissions" not in request.model_fields_set


def test_queue_item_has_independent_screen_attestation(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    queue = CodeTurnQueue(tmp_path)
    item, _ = queue.enqueue("session-1", "client-1", {"message": "x", "via_screen": True})
    assert item.via_screen is False


def test_body_bypass_is_not_positive_screen_proof() -> None:
    from chimera.api.code_api import CodeTurnRequest

    request = CodeTurnRequest(message="x", bypass_permissions=True)
    assert "via_screen" not in request.model_dump()


def test_patch_permissions_refused_without_screen_scope(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.config import Settings
    from chimera.interface import ChatSession

    app = build_api_app(
        lambda: ChatSession(None), settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),
        workspace=tmp_path, screen=ScreenCredential("screen-secret"),
    )
    response = TestClient(app).patch(
        "/api/code/sessions/missing/permissions", json={"bypass_permissions": True},
    )
    assert response.status_code == 403


def test_patch_permissions_accepted_with_screen_scope(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import HEADER_NAME, ScreenCredential
    from chimera.config import Settings
    from chimera.interface import ChatSession

    screen = ScreenCredential("screen-secret")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    app = build_api_app(
        lambda: ChatSession(None), settings=settings, workspace=tmp_path, screen=screen,
    )
    from chimera.core.code_session import CodeSession

    app.state.code_sessions.save(
        CodeSession(None, session_id="session-1", workspace=str(tmp_path))  # type: ignore[arg-type]
    )
    response = TestClient(app).patch(
        "/api/code/sessions/session-1/permissions", json={"bypass_permissions": True},
        headers={HEADER_NAME: "screen-secret"},
    )
    assert response.status_code == 200
    assert response.json()["bypass_permissions"] is True

    # Snapshot a session file while enabled, then switch off and restore the snapshot. Disk content
    # must not be authority: the current process state remains off despite replaying old JSON.
    session_path = app.state.code_sessions._path("session-1")
    enabled_snapshot = session_path.read_bytes()
    response = TestClient(app).patch(
        "/api/code/sessions/session-1/permissions", json={"bypass_permissions": False},
        headers={HEADER_NAME: "screen-secret"},
    )
    assert response.status_code == 200
    session_path.write_bytes(enabled_snapshot)
    assert app.state.code_sessions.stored_bypass_permissions("session-1") is False


def test_bridge_send_cannot_enable_bypass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from chimera.api.code_api import CodeTurnRequest

    direct = CodeTurnRequest(message="hello", source="bridge", bypass_permissions=True)
    queued = CodeTurnRequest.model_validate({
        "message": "hello", "source": "bridge", "mode": "queue", "bypass_permissions": True,
    })
    for request in (direct, queued):
        # The HTTP endpoint stamps this after excluding every bridge origin; body provenance is
        # deliberately mutable and never a positive attestation.
        request._via_screen = False
        request.bypass_permissions = False
        assert request.bypass_permissions is False
    from chimera.core.code_session import CodeSession

    session = CodeSession(None)  # type: ignore[arg-type]
    session.bypass_permissions = False
    assert "bypass_permissions" not in session.to_dict()


def test_channel_and_work_turns_do_not_inherit_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.api.code_api import CodeTurnRequest

    channel = CodeTurnRequest(message="channel", session_id="saved", bypass_permissions=True)
    channel = channel.stamp_channel_origin("test-channel")
    channel._via_screen = False
    channel.bypass_permissions = False
    assert channel.bypass_permissions is False
    monkeypatch.setenv("CHIMERA_APPROVAL_WAIT", "0.25")
    from chimera.config import Settings

    settings = Settings(CHIMERA_APPROVAL_WAIT=0.25)
    assert settings.approval_wait == 0.25
    # Work requests are newly constructed from work metadata, not the owner's persistent preference.
    work = CodeTurnRequest(message="background work", session_id="saved", bypass_permissions=False)
    assert work.bypass_permissions is False
    assert work._via_screen is False


def test_denylist_and_floor_still_refuse_with_bypass() -> None:
    from chimera.governance.ledger_tool import LedgeredTool
    from chimera.governance.policy import Decision, RuleSet

    wrapped = LedgeredTool.__new__(LedgeredTool)
    wrapped.bypass_permissions = True
    destructive = RuleSet().evaluate("run_shell: rm -rf /")
    assert destructive is not None and destructive.decision in {Decision.BLOCK, Decision.WARN}
    assert wrapped._bypass_safe("run_shell", {"command": "rm -rf /"}) is False
    # Credential-floor rules refuse reading an owner .env even when the mode would otherwise bypass.
    assert wrapped._bypass_safe("read_file", {"path": ".env"}) is False


def test_open_pull_request_is_released_by_bypass_and_the_approver_is_unchanged() -> None:
    # Owner decision (26/10): with the switch ON, opening a pull request runs without a card. The
    # approver itself is unchanged, so without the switch the same call still asks.
    from chimera.governance.approval import always_ask
    from chimera.governance.ledger_tool import LedgeredTool
    from chimera.governance.policy import Decision, Verdict

    asked: list[str] = []
    approve = always_ask(
        Path("unused"), mode="allow", ask_with=lambda action, _reason: asked.append(action) or False,
    )
    action = "open_pull_request: title=test"
    bypassed = LedgeredTool.__new__(LedgeredTool)
    bypassed.bypass_permissions = True
    assert bypassed._bypass_safe("open_pull_request", {"title": "test"}) is True
    assert approve(Verdict(Decision.REVIEW, "owner must approve"), action) is False
    assert asked == [action]


def test_stopping_the_turn_releases_an_endless_wait(tmp_path: Path) -> None:
    import threading

    from chimera.api.live_turns import LiveTurns

    turns = LiveTurns()
    turn_id = "turn-1"
    turns.start(turn_id=turn_id, session_id="session-1", workspace=str(tmp_path), message="x", live_since=0)
    stopped = threading.Event()
    turns.on_stop(turn_id, stopped.set)
    entered_wait = threading.Event()
    ended_wait = threading.Event()
    result: list[bool] = []

    def wait_for_approval() -> None:
        entered_wait.set()
        result.append(stopped.wait())
        ended_wait.set()

    waiter = threading.Thread(target=wait_for_approval, daemon=True)
    waiter.start()
    assert entered_wait.wait(1)
    assert not ended_wait.is_set()
    assert turns.stop(turn_id) == "stopping"
    assert ended_wait.wait(1), "stopping the turn must release an unbounded approval wait"
    waiter.join(timeout=1)
    assert result == [True]
    assert turns.should_stop(turn_id)()

def test_code_turn_registry_excludes_in_process_interpreter(tmp_path: Path) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    registry, _ = assemble_registry(
        CodeSeams(), tmp_path, settings, LLMGateway(), steps=1, surface="api:turn",
    )
    assert "code_interpreter" not in registry.names()
    assert "execute_code" in registry.names()


def test_forged_persisted_bypass_flag_is_ignored(tmp_path):
    import json

    from chimera.core.code_session import CodeSessionStore

    store = CodeSessionStore(tmp_path)
    path = tmp_path / "forged.json"
    path.write_text(json.dumps({"session_id": "forged", "bypass_permissions": True}), encoding="utf-8")
    assert store.stored_bypass_permissions("forged") is False


def test_forged_via_screen_queue_flag_is_ignored(tmp_path):
    import json

    from chimera.api.code_session_queue import CodeTurnQueue

    path = tmp_path / "forged.queue.jsonl"
    path.write_text(json.dumps({
        "id": "q1", "session_id": "forged", "client_id": "c1", "source": "app",
        "request": {}, "state": "pending", "created_at": 1, "via_screen": True,
    }) + "\n", encoding="utf-8")
    item = CodeTurnQueue(tmp_path).get("forged", "q1")
    assert item is not None and item.via_screen is False


# --- Owner decision (27/10/2026): the permission switch applies to a turn the desktop bridge forwards
# in a conversation the owner switched on from the screen. The bridge sets nothing: it can only be
# granted the switch by scope keys this process sets, never by its body, and never the browser.


class _StoredSwitch:
    """A conversation store that knows only which conversations the owner switched on."""

    def __init__(self, on: set[str]) -> None:
        self.on = on

    def stored_bypass_permissions(self, session_id: str) -> bool:
        return session_id in self.on


def test_bridge_turn_still_refuses_the_denylist_and_the_floor_with_the_switch_on() -> None:
    from chimera.governance.ledger_tool import LedgeredTool

    wrapped = LedgeredTool.__new__(LedgeredTool)
    wrapped.bypass_permissions = True
    assert wrapped._bypass_safe("run_shell", {"command": "rm -rf /"}) is False
    assert wrapped._bypass_safe("read_file", {"path": ".env"}) is False


def test_paired_device_and_bare_token_never_inherit_the_switch() -> None:
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.code_api import turn_bypass_switch, turn_origin
    from chimera.api.devices import DEVICE_SCOPE_KEY

    store = _StoredSwitch({"session-1"})
    # A paired phone, alone or relayed through the bridge: no browser, no switch.
    assert turn_origin({DEVICE_SCOPE_KEY: "device-1"}) == (False, False)
    assert turn_origin({DEVICE_SCOPE_KEY: "device-1", VIA_BRIDGE_SCOPE_KEY: True}) == (False, False)
    # A bearer token with no scope mark (readable by the agent's shell): nothing.
    assert turn_origin({}) == (False, False)
    for origin in ({DEVICE_SCOPE_KEY: "device-1"}, {}):
        _, via_bridge = turn_origin(origin)
        switch = turn_bypass_switch(
            via_screen=False, via_bridge=via_bridge, session_id="session-1", store=store,
        )
        assert switch() is False


def test_bridge_scope_cannot_lend_the_browser_and_screen_turns_keep_theirs() -> None:
    from chimera.api import screen_credential
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.code_api import turn_origin

    # Bridged: the switch may follow, the owner's Chrome never does.
    assert turn_origin({VIA_BRIDGE_SCOPE_KEY: True, screen_credential.SCOPE_KEY: True}) == (False, True)
    # The owner's screen: the browser, and no bridge mark.
    assert turn_origin({screen_credential.SCOPE_KEY: True}) == (True, False)


def test_the_bridge_cannot_switch_the_mode_on(tmp_path: Path) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from chimera.api.app import build_api_app
    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
    from chimera.api.screen_credential import HEADER_NAME, ScreenCredential
    from chimera.config import Settings
    from chimera.core.code_session import CodeSession, session_bypass
    from chimera.interface import ChatSession

    app = build_api_app(
        lambda: ChatSession(None),
        settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),
        workspace=tmp_path,
        screen=ScreenCredential("screen-secret"),
    )
    app.state.code_sessions.save(
        CodeSession(None, session_id="bridged-1", workspace=str(tmp_path))  # type: ignore[arg-type]
    )

    async def bridged(scope, receive, send):  # type: ignore[no-untyped-def]
        # What the desktop bridge does in-process: its scope mark, even with the screen's header sent.
        if scope["type"] == "http":
            scope = {**scope, VIA_BRIDGE_SCOPE_KEY: True}
        await app(scope, receive, send)

    response = TestClient(bridged).patch(
        "/api/code/sessions/bridged-1/permissions", json={"bypass_permissions": True},
        headers={HEADER_NAME: "screen-secret"},
    )
    assert response.status_code == 403
    assert session_bypass("bridged-1") is False


def test_bridged_queue_provenance_stays_in_process_memory(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    item, _ = CodeTurnQueue(tmp_path).enqueue(
        "session-1", "request-bridge", {"message": "desktop_send"},
        source="bridge", via_bridge=True,
    )
    assert item.via_bridge is True
    reopened = CodeTurnQueue(tmp_path).get("session-1", item.id)
    assert reopened is not None and reopened.via_bridge is True
    # A fresh process has no in-memory authority, whatever the file says.
    import chimera.api.code_session_queue as queue_module

    queue_module._BRIDGE_QUEUE_IDS.clear()
    restarted = CodeTurnQueue(tmp_path).get("session-1", item.id)
    assert restarted is not None and restarted.via_bridge is False


def test_forged_bridge_queue_flag_is_ignored(tmp_path: Path) -> None:
    import json

    from chimera.api.code_session_queue import CodeTurnQueue

    path = tmp_path / "forged-bridge.queue.jsonl"
    path.write_text(json.dumps({
        "id": "q2", "session_id": "forged-bridge", "client_id": "c2", "source": "bridge",
        "request": {}, "state": "pending", "created_at": 1, "via_bridge": True,
    }) + "\n", encoding="utf-8")
    item = CodeTurnQueue(tmp_path).get("forged-bridge", "q2")
    assert item is not None and item.via_bridge is False


def test_bridged_mark_needs_a_bridge_labelled_item(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    with pytest.raises(ValueError):
        CodeTurnQueue(tmp_path).enqueue("session-1", "request-x", {"message": "x"}, source="app", via_bridge=True)


# --- Integration: a real /api/code/turn, a real run_shell through the registry the API built, and
# the owner's approver. The fake agent calls the tool itself; nothing below stubs the governance.


class _ShellAgent:
    """Runs one ``run_shell`` through the registry the API built for the turn, and records the answer.

    ``taint`` first reads untrusted content into the run's ledger, so the shell call is one the taint
    narrowing holds for the owner's approval (the case the switch exists for).
    """

    command = ""
    taint = False
    outcomes: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.registry = args[1] if len(args) > 1 else None

    def run(self, task: str, **_kw: Any) -> Any:
        from chimera.core.agent import AgentResult

        cls = type(self)
        if cls.taint:
            self.registry.get("write_file").ledger.record_fetch("body from https://example.test/")
        cls.outcomes.append(str(self.registry.get("run_shell").run(command=cls.command)))
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


def _shell_app(tmp_path: Path, monkeypatch: Any, **env: str) -> tuple[Any, Path, Path]:
    import sys

    import chimera.core
    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    home = tmp_path / "home"
    project = tmp_path / "shop"
    project.mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _ShellAgent, raising=True)
    _ShellAgent.outcomes = []
    _ShellAgent.taint = True
    script = "import pathlib; pathlib.Path('marker.txt').write_text('ran')"
    _ShellAgent.command = f'"{sys.executable}" -c "{script}"'
    settings = Settings(
        CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_APPROVAL_WAIT=0.5,
        # The test has no terminal: host execution is allowed here, so the only gate left is governance.
        CHIMERA_HOST_EXEC="allow", **env,
    )
    app = build_api_app(
        lambda: ChatSession(_ShellAgent()), workspace=tmp_path, settings=settings,
        screen=ScreenCredential("screen-secret"),
    )
    return app, project, home


def _bridged(app: Any) -> Any:
    """The desktop bridge's in-process request: its scope mark, and nothing else."""
    from fastapi.testclient import TestClient

    from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY

    async def bridged(scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] == "http":
            scope = {**scope, VIA_BRIDGE_SCOPE_KEY: True}
        await app(scope, receive, send)

    return TestClient(bridged)


def _saved_conversation(app: Any, session_id: str, project: Path, *, switch_on: bool) -> None:
    from fastapi.testclient import TestClient

    from chimera.api.screen_credential import HEADER_NAME
    from chimera.core.code_session import CodeSession

    app.state.code_sessions.save(
        CodeSession(None, session_id=session_id, workspace=str(project))  # type: ignore[arg-type]
    )
    if switch_on:
        response = TestClient(app).patch(
            f"/api/code/sessions/{session_id}/permissions", json={"bypass_permissions": True},
            headers={HEADER_NAME: "screen-secret"},
        )
        assert response.status_code == 200, response.text


def _bridged_turn(client: Any, session_id: str, project: Path, **body: Any) -> None:
    response = client.post("/api/code/turn", json={
        "message": "clean up the build folder", "session_id": session_id,
        "workspace": str(project), "stream": False, **body,
    })
    assert response.status_code == 200, response.text


def _bypassed(home: Path) -> list[dict[str, Any]]:
    """The audit's record of an action the owner's switch released (the only proof it did)."""
    from chimera.governance.audit import AuditLog

    path = home / "audit.jsonl"
    if not path.exists():
        return []
    return [r for r in AuditLog(path).entries() if r.get("type") == "bypass_permissions"]


def test_bridge_turn_with_the_switch_on_runs_the_shell_without_asking(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app, project, home = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "bridged-on", project, switch_on=True)

    _bridged_turn(_bridged(app), "bridged-on", project)

    assert (project / "marker.txt").read_text(encoding="utf-8") == "ran"
    assert _bypassed(home), "the owner's switch was on: the release must be audited"
    assert "did NOT run" not in _ShellAgent.outcomes[0]


def test_bridge_turn_with_the_switch_off_still_asks(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "bridged-off", project, switch_on=False)

    _bridged_turn(_bridged(app), "bridged-off", project)

    assert not (project / "marker.txt").exists(), "without the switch the shell must not run"
    assert not _bypassed(home), "without the switch nothing may be released by bypass"
    assert "did NOT run" in _ShellAgent.outcomes[0]


def test_a_body_bypass_from_the_bridge_is_ignored_when_the_switch_is_off(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app, project, home = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "body-off", project, switch_on=False)

    _bridged_turn(_bridged(app), "body-off", project, bypass_permissions=True)

    assert not (project / "marker.txt").exists(), "a body field must never switch the mode on"
    assert not _bypassed(home), "the bridge's body asked for bypass; nothing may be released"
    assert "did NOT run" in _ShellAgent.outcomes[0]


def test_the_switch_of_one_conversation_does_not_reach_another(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app, project, home = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "with-switch", project, switch_on=True)
    _saved_conversation(app, "without-switch", project, switch_on=False)

    _bridged_turn(_bridged(app), "without-switch", project)

    assert not (project / "marker.txt").exists()
    assert not _bypassed(home)
    assert "did NOT run" in _ShellAgent.outcomes[0]


def test_the_bridge_cannot_switch_the_mode_off_either(tmp_path: Path, monkeypatch: Any) -> None:
    from chimera.core.code_session import session_bypass

    app, project, _home = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "bridge-off-attempt", project, switch_on=True)

    response = _bridged(app).patch(
        "/api/code/sessions/bridge-off-attempt/permissions", json={"bypass_permissions": False},
    )
    assert response.status_code == 403
    assert session_bypass("bridge-off-attempt") is True


def test_only_the_owner_screen_route_writes_the_switch() -> None:
    # Every writer of the stored switch is the owner-only PATCH. A second caller would be a route
    # the bridge (or a token) could reach, and this is the place to notice it.
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[1] / "chimera"
    callers = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "set_session_bypass(" in path.read_text(encoding="utf-8")
    )
    assert callers == ["api/code_api.py", "core/code_session.py"], callers
    text = (root / "api" / "code_api.py").read_text(encoding="utf-8")
    assert text.count("set_session_bypass(session_id, enabled)") == 1


def test_a_bridged_and_a_screen_item_in_one_conversation_keep_their_own_marks(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    queue = CodeTurnQueue(tmp_path)
    bridged, _ = queue.enqueue("shared", "from-bridge", {"message": "b"}, source="bridge", via_bridge=True)
    screen, _ = queue.enqueue("shared", "from-screen", {"message": "s"}, via_screen=True)
    reopened = {item.id: item for item in CodeTurnQueue(tmp_path).list("shared")}
    assert (reopened[bridged.id].via_bridge, reopened[bridged.id].via_screen) == (True, False)
    assert (reopened[screen.id].via_bridge, reopened[screen.id].via_screen) == (False, True)


# --- Queue marks: process memory only, dropped when an item finishes, and never widened by a retry.


def test_a_retry_after_restart_returns_the_stored_item_unmarked(tmp_path: Path) -> None:
    import chimera.api.code_session_queue as queue_module
    from chimera.api.code_session_queue import CodeTurnQueue

    item, created = CodeTurnQueue(tmp_path).enqueue(
        "session-1", "retry-1", {"message": "x"}, source="bridge", via_bridge=True,
    )
    assert created
    queue_module._BRIDGE_QUEUE_IDS.clear()  # the restart: the mark is gone, the file is not

    again, created_again = CodeTurnQueue(tmp_path).enqueue(
        "session-1", "retry-1", {"message": "x"}, source="bridge", via_bridge=True,
    )
    assert created_again is False and again.id == item.id
    assert again.via_bridge is False, "a retry must not restore the mark the item lost"


def test_a_retry_that_changes_the_request_or_drops_a_held_mark_is_still_refused(tmp_path: Path) -> None:
    from chimera.api.code_session_queue import CodeTurnQueue

    queue = CodeTurnQueue(tmp_path)
    queue.enqueue("session-1", "retry-2", {"message": "x"}, source="bridge", via_bridge=True)
    with pytest.raises(ValueError):
        queue.enqueue("session-1", "retry-2", {"message": "y"}, source="bridge", via_bridge=True)
    with pytest.raises(ValueError):
        queue.enqueue("session-1", "retry-2", {"message": "x"}, source="bridge", via_bridge=False)


def test_finished_or_cancelled_items_drop_their_marks(tmp_path: Path) -> None:
    import chimera.api.code_session_queue as queue_module
    from chimera.api.code_session_queue import CodeTurnQueue

    queue = CodeTurnQueue(tmp_path)
    done, _ = queue.enqueue("session-1", "d", {"message": "d"}, source="bridge", via_bridge=True)
    failed, _ = queue.enqueue("session-1", "f", {"message": "f"}, via_screen=True)
    cancelled, _ = queue.enqueue("session-1", "c", {"message": "c"}, source="bridge", via_bridge=True)
    assert queue.claim_next("session-1") is not None  # done
    assert queue.finish("session-1", done.id)
    assert done.id not in queue_module._BRIDGE_QUEUE_IDS
    assert queue.claim_next("session-1") is not None  # failed
    assert queue.fail("session-1", failed.id)
    assert failed.id not in queue_module._SCREEN_QUEUE_IDS
    assert queue.cancel("session-1", cancelled.id)
    assert cancelled.id not in queue_module._BRIDGE_QUEUE_IDS
    # The client ids stay reserved: a retry of a finished item still returns it, unmarked.
    again, created = queue.enqueue("session-1", "d", {"message": "d"}, source="bridge", via_bridge=True)
    assert created is False and again.via_bridge is False
