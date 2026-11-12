"""A turn parked on its plan's approval comes back whole to a screen that opens late.

Reported: reload the window while a big task waits for the plan to be approved, and the plan panel
and the approval card are gone. The screen builds both from the conversation's live stream alone
(`getCodeSession` names the running turn and its `live_since`; the frames come from
`/api/code/sessions/{id}/live?since=`), so the server has to replay the turn's `turn_started`, its
`plan` frames and its `approval` frame to a subscriber that was not there when they were sent.

This drives the real route and the real durable question (`ask_durably`), parks the turn on it,
and reads back what a fresh window reads.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.plan_gate import _PLAN_GATE_SYSTEM
from chimera.config import Settings, get_settings
from chimera.core import Agent
from chimera.governance import pending
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult
from chimera.tools import ToolRegistry

PLAN = "1. read a.py\n2. edit a.py\n3. run pytest"
TIMEOUT = 10.0


class _Gateway:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        first = messages[0]
        system = first.get("content") if isinstance(first, dict) else first.content
        text = PLAN if system == _PLAN_GATE_SYSTEM else "done"
        return CompletionResult(content=text, model="test/m", prompt_tokens=1, completion_tokens=1)


def _frames(app: Any, session_id: str, since: int) -> list[dict[str, Any]]:
    """What a window opening now is sent first by `/live?since=`: `live_frames` yields exactly
    `bus.replay` before it waits for anything new (a TestClient cannot hold an SSE stream open)."""
    return list(app.state.session_bus.replay(session_id, since))


def test_a_turn_waiting_on_its_plan_is_replayed_whole_to_a_window_that_opens_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(
        lambda: ChatSession(Agent(_Gateway(), ToolRegistry())), workspace=ws, settings=settings
    )
    client = TestClient(app)
    # The first window: it sends the turn and is then gone (the reload). The turn is not its.
    box: list[str] = []
    thread = threading.Thread(
        target=lambda: box.append(client.post(
            "/api/code/turn", json={"message": "refatorar o módulo de cache", "stream": False}
        ).text),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline and not pending.pending(home):
        time.sleep(0.02)
    assert pending.pending(home), box
    [question] = pending.pending(home)

    try:
        running = client.get("/api/code/turns/running").json()
        assert len(running) == 1, "the turn waiting on its plan must still be listed as running"
        session = client.get(f"/api/code/sessions/{running[0]['session_id']}").json()
        assert session["running_turn"]["turn_id"] == running[0]["turn_id"]

        frames = _frames(app, running[0]["session_id"], session["running_turn"]["live_since"])
        events = [f["event"] for f in frames]
        assert events[0] == "turn_started"
        plans = [f["payload"] for f in frames if f["event"] == "plan"]
        assert plans, "the plan frame must reach a window that was not there when it was sent"
        assert plans[-1]["approval_id"] == question.id
        approval = [f["payload"] for f in frames if f["event"] == "approval"]
        assert approval and approval[-1]["id"] == question.id
    finally:
        pending.answer(home, question.id, False)
        thread.join(TIMEOUT)
