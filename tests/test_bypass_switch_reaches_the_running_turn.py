"""The owner's permission switch, for a conversation that is running or not yet saved.

Three things the switch promised and the code did not keep:

- a conversation whose first turn is still running has no file yet, and the list that the screen
  reads did not say what its switch was, so the label could never show "on";
- the running turn read the switch once, when it was built, so turning it on mid-turn released
  nothing until the next turn;
- the PATCH for such a conversation was accepted, but nothing the screen read came back with it.

These tests hold the server half: the PATCH on a live conversation, the list that carries the flag,
the flag surviving the turn's end in this process, and the approver and the ledger reading the
switch at each question rather than at build time. Owner-only stays: the bridge cannot set it, and
`test_code_permission_screen_security.py` keeps proving that.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


def _app(tmp_path: Path) -> Any:
    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.config import Settings
    from chimera.interface import ChatSession

    return build_api_app(
        lambda: ChatSession(None),
        settings=Settings(CHIMERA_HOME=str(tmp_path / "home")),
        workspace=tmp_path,
        screen=ScreenCredential("screen-secret"),
    )


def test_the_owner_approver_reads_the_switch_at_each_question(tmp_path: Path) -> None:
    from chimera.api.code_api import bypass_owner_for
    from chimera.governance.audit import AuditLog

    flag = {"on": False}
    asked: list[object] = []

    def original(*args: Any) -> bool:
        asked.append(args)
        return False

    sink = _Sink()
    owner = bypass_owner_for(
        original,
        bypass_now=lambda: flag["on"],
        home=tmp_path,
        ws=tmp_path,
        audit=AuditLog(tmp_path / "audit.jsonl"),
        approval_sink=sink,
        run_id="run-1",
    )
    question = SimpleNamespace(
        decision=SimpleNamespace(value="review"),
        source=SimpleNamespace(value="agent"),
        action="run_shell: pytest -q",
    )

    # Switch off when the question is asked: the owner is asked, as before.
    assert owner(question, "run_shell: pytest -q") is False
    assert len(asked) == 1

    # Switched on mid-turn: the next question is released without a card, and says so.
    flag["on"] = True
    assert owner(question, "run_shell: pytest -q") is True
    assert len(asked) == 1, "a released question must not also be asked"
    assert any("aprovado sozinho" in str(event.get("text", "")) for event in sink.events)


def test_the_ledger_reads_the_switch_at_each_call_not_at_build() -> None:
    from chimera.governance.ledger_tool import LedgeredTool

    flag = [False]
    wrapped = LedgeredTool.__new__(LedgeredTool)
    wrapped.bypass_permissions = lambda: flag[0]  # type: ignore[assignment]
    assert wrapped.bypass_permissions is False
    flag[0] = True
    assert wrapped.bypass_permissions is True


def test_a_static_flag_still_works_for_the_ledger() -> None:
    from chimera.governance.ledger_tool import LedgeredTool

    wrapped = LedgeredTool.__new__(LedgeredTool)
    wrapped.bypass_permissions = True
    assert wrapped.bypass_permissions is True


def test_patch_on_a_running_unsaved_conversation_is_kept_and_listed(tmp_path: Path) -> None:
    from chimera.core.code_session import session_bypass

    app = _app(tmp_path)
    # The first turn is running and nothing is saved yet: the conversation exists only as a live turn.
    app.state.live_turns.start(
        turn_id="turn-live", session_id="live-1", workspace=str(tmp_path), message="build it", live_since=0,
    )
    client = TestClient(app)
    from chimera.api.screen_credential import HEADER_NAME

    response = client.patch(
        "/api/code/sessions/live-1/permissions", json={"bypass_permissions": True},
        headers={HEADER_NAME: "screen-secret"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"id": "live-1", "bypass_permissions": True}
    assert session_bypass("live-1") is True

    rows = {row["id"]: row for row in client.get("/api/code/sessions").json()}
    assert rows["live-1"]["running"] is True
    assert rows["live-1"]["bypass_permissions"] is True, "the list must carry the switch it was asked to show"


def test_the_switch_survives_the_turn_ending_in_this_process(tmp_path: Path) -> None:
    from chimera.core.code_session import CodeSession, session_bypass

    app = _app(tmp_path)
    app.state.live_turns.start(
        turn_id="turn-end", session_id="ends-1", workspace=str(tmp_path), message="x", live_since=0,
    )
    client = TestClient(app)
    from chimera.api.screen_credential import HEADER_NAME

    client.patch(
        "/api/code/sessions/ends-1/permissions", json={"bypass_permissions": True},
        headers={HEADER_NAME: "screen-secret"},
    )
    app.state.live_turns.finish("turn-end")
    app.state.code_sessions.save(
        CodeSession(None, session_id="ends-1", workspace=str(tmp_path))  # type: ignore[arg-type]
    )
    assert session_bypass("ends-1") is True
    rows = {row["id"]: row for row in client.get("/api/code/sessions").json()}
    assert rows["ends-1"]["bypass_permissions"] is True
