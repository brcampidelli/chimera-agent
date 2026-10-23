"""Security regressions for the owner's screen-only permission mode."""

from __future__ import annotations

from pathlib import Path

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


def test_open_pull_request_still_asks_with_bypass() -> None:
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
    # Even with the session switch active, this owner-level approval remains explicit.
    assert bypassed._bypass_safe("open_pull_request", {"title": "test"}) is False
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
