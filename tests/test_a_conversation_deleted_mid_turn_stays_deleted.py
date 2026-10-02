"""A conversation deleted while one of its turns runs stays deleted.

Found reading the code on 2026-09-30 (R18 of the review of several conversations at once). Deleting a
conversation did not look at running turns. The turn went on, and when it finished it saved the
conversation again (and its receipt after that), so a conversation the person had deleted came back.

Now deleting it stops the running turn (the same signal as Stop) and that turn writes nothing more of
the conversation. The same holds for deleting a whole project's conversations.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 10.0


class _Held:
    entered = threading.Event()
    release = threading.Event()
    stopped = threading.Event()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, should_stop: Any = None, **_kw: Any) -> AgentResult:
        if "hold" in task:
            type(self).entered.set()
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline and not type(self).release.is_set():
                if should_stop is not None and should_stop():
                    type(self).stopped.set()
                    break
                time.sleep(0.01)
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def held() -> type[_Held]:
    for flag in (_Held.entered, _Held.release, _Held.stopped):
        flag.clear()
    yield _Held
    _Held.release.set()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type) -> tuple[TestClient, Path, Path]:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(agent()), workspace=ws, settings=settings)), home, ws


def _files(home: Path, session_id: str) -> list[Path]:
    return list(home.rglob(f"{session_id}.json"))


def _opened(client: TestClient, ws: Path) -> str:
    text = client.post("/api/code/turn", json={"message": "hello", "workspace": str(ws), "stream": False}).text
    return re.search(r'"session_id": ?"([^"]+)"', text).group(1)  # type: ignore[union-attr]


def _hold(client: TestClient, ws: Path, session_id: str, held: type[_Held]) -> threading.Thread:
    thread = threading.Thread(
        target=lambda: client.post(
            "/api/code/turn",
            json={"message": "hold", "workspace": str(ws), "session_id": session_id, "stream": False},
        ),
        daemon=True,
    )
    thread.start()
    assert held.entered.wait(TIMEOUT)
    return thread


def test_deleting_a_conversation_mid_turn_stops_the_turn_and_it_does_not_come_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: type[_Held]
) -> None:
    client, home, ws = _client(tmp_path, monkeypatch, held)
    session_id = _opened(client, ws)
    assert _files(home, session_id)
    turn = _hold(client, ws, session_id, held)

    assert client.delete(f"/api/code/sessions/{session_id}").json() == {"ok": True}

    assert held.stopped.wait(TIMEOUT), "the running turn of a deleted conversation was not stopped"
    turn.join(TIMEOUT)
    assert not turn.is_alive()
    assert _files(home, session_id) == [], "the deleted conversation came back when its turn finished"


def test_deleting_a_project_mid_turn_does_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: type[_Held]
) -> None:
    client, home, ws = _client(tmp_path, monkeypatch, held)
    session_id = _opened(client, ws)
    turn = _hold(client, ws, session_id, held)

    assert client.delete("/api/code/projects", params={"workspace": str(ws)}).json()["deleted"] >= 1

    assert held.stopped.wait(TIMEOUT)
    turn.join(TIMEOUT)
    assert _files(home, session_id) == []


def test_a_conversation_that_was_not_deleted_is_saved_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, held: type[_Held]
) -> None:
    """The control: the turn above ends because it was stopped, and the file is gone because the
    turn was told not to write it, not because turns stopped saving."""
    client, home, ws = _client(tmp_path, monkeypatch, held)
    session_id = _opened(client, ws)
    turn = _hold(client, ws, session_id, held)
    held.release.set()
    turn.join(TIMEOUT)
    stored = _files(home, session_id)
    assert stored and "hold" in stored[0].read_text(encoding="utf-8")
