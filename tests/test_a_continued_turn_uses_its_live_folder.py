"""A turn that continues a conversation by its id alone runs in the conversation's folder, even when
the turn that opened it has not saved yet.

Measured 2026-10-25: a new worktree conversation's first turn was stopped during project setup, and
a continuation sent at once carried only the session id. The first turn saves its folder only when
it ends, so the continuation read an empty stored folder and ran in the app's default workspace.
The folder the conversation runs in is also held by the turn that is live for it, so that folder is
used before the default one.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import AgentResult
from tests.conversation_fakes import TIMEOUT, Agent, App

#: A coding turn spends seconds in setup before its agent runs (project checks, the folder lock).
SETUP_TIMEOUT = 60.0


def _held_agent() -> type:
    """An agent whose "hold" turn waits on a gate and ignores its stop signal, so the turn stays live
    and unsaved until the test lets it finish. Records the folder each turn ran in."""

    class HeldAgent(Agent):
        gate = threading.Event()
        entered = threading.Event()
        roots: list[tuple[str, str]] = []

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.root = str(getattr(self.config, "project_root", "") or "")

        def run(self, task: str, **_kw: Any) -> AgentResult:
            type(self).roots.append((task, self.root))
            if "hold" in task:
                type(self).entered.set()
                type(self).gate.wait(SETUP_TIMEOUT)
            return AgentResult(
                answer="done", steps=1, stopped_reason="final",
                transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
            )

    return HeldAgent


def _ran_in(agent: type, message: str) -> str:
    for task, root in reversed(agent.roots):
        if message in task:
            return root
    raise AssertionError(f"no turn ran with {message!r}: {agent.roots}")


def test_a_continuation_during_a_live_unsaved_first_turn_uses_its_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _held_agent()
    app = App(tmp_path, monkeypatch, agent)
    wt = tmp_path / "wt"
    wt.mkdir()
    replies: dict[str, str] = {}

    def first() -> None:
        body = {"message": f"hold [{wt}]", "workspace": str(wt), "stream": False}
        replies["first"] = app.client.post("/api/code/turn", json=body).text

    thread = threading.Thread(target=first, daemon=True)
    thread.start()
    try:
        assert agent.entered.wait(SETUP_TIMEOUT)
        live = app.app.state.live_turns
        running = [t for t in live.running() if t.message.startswith("hold")]
        assert len(running) == 1
        sid = running[0].session_id
        # The first turn has not saved: nothing stored names a folder for this conversation yet.
        assert not (app.home / "code_sessions" / f"{sid}.json").exists()
        assert live.stop(running[0].turn_id) == "stopping"

        body = {"message": "again", "session_id": sid, "stream": False}
        cont = app.client.post("/api/code/turn", json=body).text
        assert "done" in cont, cont[:300]
        assert _ran_in(agent, "again") == str(wt)
    finally:
        agent.gate.set()
        thread.join(TIMEOUT)


def test_a_continuation_after_the_first_turn_saved_uses_the_stored_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = _held_agent()
    app = App(tmp_path, monkeypatch, agent)
    wt = tmp_path / "wt"
    wt.mkdir()
    app.folder = wt
    sid = app.turn("first")
    assert app.rows()[sid]["workspace"] == str(wt)

    body = {"message": "again", "session_id": sid, "stream": False}
    assert "done" in app.client.post("/api/code/turn", json=body).text
    assert _ran_in(agent, "again") == str(wt)
