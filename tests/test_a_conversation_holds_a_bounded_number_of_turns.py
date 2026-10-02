"""A conversation holds a bounded number of turns running or waiting.

Found reading the code on 2026-09-30 (R17 of the review of several conversations at once). Every
turn request starts a thread, and turns of one conversation wait for each other on its lock. The
owner's screen queues a follow-up in the composer and sends it when the turn before it ends, so it
never has more than one in flight — but a share link reaches the guest route over the network, and
nothing bounded how many turns a guest could pile onto one conversation: each one a thread, waiting.

Now one conversation holds at most four (one running, three waiting). A fifth is refused with 429
before anything is built or announced, so the refusal leaves no half-started turn behind; another
conversation is not affected, and once a turn ends there is room again.

Measured, and not changed: the per-conversation lock dictionary holds one small lock per
conversation ever touched in this process — about a megabyte at ten thousand conversations.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 15.0


class _Held:
    release = threading.Event()
    running = 0
    explode = False
    guard = threading.Lock()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        if type(self).explode and (len(_a) > 2 or "config" in kwargs):
            raise RuntimeError("the agent could not be built")
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:
        if "hold" in task:
            with type(self).guard:
                type(self).running += 1
            type(self).release.wait(TIMEOUT)
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Held, raising=True)
    _Held.release.clear()
    _Held.running = 0
    _Held.explode = False
    ws = tmp_path / "ws"
    ws.mkdir()
    app = build_api_app(lambda: ChatSession(_Held()), workspace=ws,
                        settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"))
    yield TestClient(app), ws, tmp_path
    _Held.release.set()
    _Held.explode = False


def _opened(client: TestClient, ws: Path) -> str:
    return str(client.post("/api/code/turn", json={"message": "hello", "workspace": str(ws), "stream": False})
               .text.split('"session_id": "')[1].split('"')[0])


def _send(client: TestClient, ws: Path, session_id: str, statuses: list[int]) -> threading.Thread:
    def go() -> None:
        r = client.post("/api/code/turn",
                        json={"message": "hold", "workspace": str(ws), "session_id": session_id, "stream": False})
        statuses.append(r.status_code)

    thread = threading.Thread(target=go, daemon=True)
    thread.start()
    return thread


def _until(predicate: Any) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_fifth_turn_on_one_conversation_is_refused_and_another_conversation_is_not(client: Any) -> None:
    http, ws, tmp_path = client
    session_id = _opened(http, ws)
    statuses: list[int] = []
    held = [_send(http, ws, session_id, statuses) for _ in range(4)]
    assert _until(lambda: _Held.running == 1), "the first turn never started"
    time.sleep(0.3)  # the other three reach their wait

    refused = http.post("/api/code/turn",
                        json={"message": "hold", "workspace": str(ws), "session_id": session_id, "stream": False})
    assert refused.status_code == 429, refused.text
    assert "4" in refused.json()["detail"]
    running = http.get("/api/code/turns/running").json()
    assert len(running) == 4, "the refused turn was announced as running"

    other_ws = tmp_path / "other"
    other_ws.mkdir()
    other = http.post("/api/code/turn", json={"message": "hello", "workspace": str(other_ws), "stream": False})
    assert other.status_code == 200, "another conversation was refused because of this one"

    _Held.release.set()
    for thread in held:
        thread.join(TIMEOUT)
    assert statuses == [200, 200, 200, 200]
    again = http.post("/api/code/turn",
                      json={"message": "hello", "workspace": str(ws), "session_id": session_id, "stream": False})
    assert again.status_code == 200, "the conversation stayed full after its turns ended"


def test_a_turn_that_fails_to_start_gives_its_place_back(client: Any) -> None:
    http, ws, _ = client
    session_id = _opened(http, ws)

    _Held.explode = True
    for _ in range(5):
        with pytest.raises(RuntimeError):
            http.post("/api/code/turn",
                      json={"message": "hi", "workspace": str(ws), "session_id": session_id, "stream": False})
    _Held.explode = False
    ok = http.post("/api/code/turn",
                   json={"message": "hello", "workspace": str(ws), "session_id": session_id, "stream": False})
    assert ok.status_code == 200, "turns that failed to start kept their places"
