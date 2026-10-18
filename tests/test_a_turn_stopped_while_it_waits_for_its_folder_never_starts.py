"""A turn stopped while it waits for its folder leaves at once, says so, and never starts (C11).

Observed 2026-10-18 (study 34, defect 2): ``desktop_job stop=true`` on a turn the bridge reported as
"WAITING FOR THE FOLDER" answered ``stop: requested`` with ``ended: false, turn_running: true`` — the
same words as a stop that has to wait for a step to finish. A turn that never started has no step in
progress: the stop should take it out of the queue on the spot and say it stopped before starting.

These run the REAL coding turn (only the agent is a double), so the folder lock, the wait, the stop
signal and the bookkeeping are the ones the app uses.
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

TIMEOUT = 10.0


class _Turn:
    """Records which turns entered the agent loop; a "hold" turn stays in it until released."""

    hold = threading.Event()
    held = threading.Event()
    entered: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def run(self, task: str, should_stop: Any = None, **_kw: Any) -> AgentResult:
        type(self).entered.append(task.split(" [")[0].split("\n")[-1])
        if "hold" in task:
            type(self).held.set()
            deadline = time.monotonic() + TIMEOUT
            while not type(self).hold.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


@pytest.fixture
def turn() -> Any:
    _Turn.hold.clear()
    _Turn.held.clear()
    _Turn.entered = []
    yield _Turn
    _Turn.hold.set()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Turn, raising=True)
    ws = tmp_path / "default-ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(_Turn()), workspace=ws, settings=settings))


def _send(client: TestClient, folder: Path, message: str) -> str:
    body = {"message": f"{message} [{folder}]", "workspace": str(folder), "stream": False}
    return client.post("/api/code/turn", json=body).text


def _waiting_turn(client: TestClient, message: str) -> str:
    """The id of the running turn whose message is ``message`` once it says it waits."""
    live = client.app.state.live_turns  # type: ignore[attr-defined]
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        for running in live.running():
            if running.message.startswith(message) and live.is_waiting(running.turn_id):
                return str(running.turn_id)
        time.sleep(0.01)
    raise AssertionError(f"no turn {message!r} waiting for its folder")


def test_a_waiting_turn_stops_at_once_and_never_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    live = client.app.state.live_turns  # type: ignore[attr-defined]
    writer = threading.Thread(target=lambda: _send(client, folder, "hold"), daemon=True)
    writer.start()
    assert turn.held.wait(TIMEOUT)

    replies: dict[str, str] = {}
    queued = threading.Thread(
        target=lambda: replies.setdefault("queued", _send(client, folder, "queued")), daemon=True
    )
    queued.start()
    turn_id = _waiting_turn(client, "queued")

    stopped = client.post(f"/api/code/turns/{turn_id}/stop")
    # The answer says it is OVER, not "stopping": nothing was in progress to finish first.
    assert stopped.status_code == 200
    assert stopped.json() == {"turn_id": turn_id, "stopping": True, "stopped_before_start": True}
    # Out of the running list by the time the stop has answered — not a poll interval later.
    assert live.get(turn_id) is None

    queued.join(TIMEOUT)
    assert "Stopped before it started" in replies["queued"]

    # The writer finishes and frees the folder: the stopped turn must not take it and run.
    turn.hold.set()
    writer.join(TIMEOUT)
    time.sleep(0.3)
    assert turn.entered == ["hold"]


def test_a_stop_of_a_running_turn_still_says_it_is_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    live = client.app.state.live_turns  # type: ignore[attr-defined]
    writer = threading.Thread(target=lambda: _send(client, folder, "hold"), daemon=True)
    writer.start()
    assert turn.held.wait(TIMEOUT)
    running = next(t for t in live.running() if t.message.startswith("hold"))

    stopped = client.post(f"/api/code/turns/{running.turn_id}/stop")

    # A turn in its loop finishes its step first: not reported as stopped before it started.
    assert stopped.json() == {
        "turn_id": running.turn_id, "stopping": True, "stopped_before_start": False,
    }
    turn.hold.set()
    writer.join(TIMEOUT)


def test_a_stop_racing_the_folder_coming_free_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, turn: Any
) -> None:
    """The writer ends in the same instant the waiting turn is stopped. Whichever the waiting turn
    sees first, it must not run: a stop that answered "stopped before it started" is a promise."""
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch)
    writer = threading.Thread(target=lambda: _send(client, folder, "hold"), daemon=True)
    writer.start()
    assert turn.held.wait(TIMEOUT)
    replies: dict[str, str] = {}
    queued = threading.Thread(
        target=lambda: replies.setdefault("queued", _send(client, folder, "queued")), daemon=True
    )
    queued.start()
    turn_id = _waiting_turn(client, "queued")

    turn.hold.set()  # the folder comes free...
    stopped = client.post(f"/api/code/turns/{turn_id}/stop")  # ...as the stop lands

    writer.join(TIMEOUT)
    queued.join(TIMEOUT)
    if stopped.status_code == 200 and stopped.json()["stopped_before_start"]:
        assert turn.entered == ["hold"], "answered 'stopped before it started' and then started"
        assert "Stopped before it started" in replies["queued"]
    else:
        # It had already taken the folder: then it is a running turn's stop, said as one.
        assert stopped.status_code in (200, 404)
