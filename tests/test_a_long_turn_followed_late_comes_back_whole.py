"""A long turn followed after its opening fell out of the live buffer still comes back whole.

Found reading the code on 2026-09-30 (R10 of the review of several conversations at once). Each
conversation keeps its last 4000 live frames for replay, and a coding turn streams one frame per
token. A screen that left a long turn and came back to it asked for everything after the turn's
opening, and the buffer no longer had the opening or the first part of the answer: the screen opens
the exchange on `turn_started`, so without it every later frame had no row to land in, and the text
that did land was the tail of the answer.

The turn's frames were never lost — the run log keeps every one. So replay now notices when the
viewer asked for frames the buffer dropped, and brings back, for each turn still present in the
buffer, its opening (pinned by the bus) and the frames the run log holds from before the buffer's
first one. A turn entirely gone from the buffer is not brought back: it has finished, and the
stored conversation holds it.

The buffers of conversations nobody watches and nothing runs in are trimmed, and a deleted
conversation's is dropped; the numbering of a conversation never restarts, so a screen holding a
number never waits for the count to climb back past it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.sharing import SessionBus
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 10.0


def _runlog(frames: dict[str, list[dict[str, Any]]]) -> Any:
    """The run log as the app keeps it: every frame of a turn, with the session number it had.
    Returned whole on purpose, so the bus's own bounds are what the tests exercise."""

    def backfill(turn_id: str, after: int, before: int) -> list[dict[str, Any]]:
        return list(frames.get(turn_id, []))

    return backfill


def _long_turn(bus: SessionBus, log: dict[str, list[dict[str, Any]]], turn: str, tokens: int) -> None:
    bus.publish("s1", "turn_started", {"message": "long one"}, turn_id=turn)
    for n in range(1, tokens + 1):
        payload = {"text": f"w{n} ", "seq": n}
        frame = bus.publish("s1", "token", payload, turn_id=turn)
        log.setdefault(turn, []).append({"event": "token", **payload, "session_seq": frame["session_seq"]})


def _text(frames: list[dict[str, Any]], turn: str) -> str:
    return "".join(str(f["payload"].get("text", "")) for f in frames if f["turn_id"] == turn)


def test_a_turn_whose_opening_fell_out_replays_from_its_opening() -> None:
    log: dict[str, list[dict[str, Any]]] = {}
    bus = SessionBus(ring=5, backfill=_runlog(log))
    _long_turn(bus, log, "t1", tokens=12)

    frames = bus.replay("s1", since=0)

    assert frames[0]["event"] == "turn_started", "the turn came back without the frame that opens it"
    assert _text(frames, "t1") == "".join(f"w{n} " for n in range(1, 13)), "the answer came back without its start"
    kept = [f["session_seq"] for f in frames]
    assert kept == sorted(kept), "a replay goes oldest first, or the screen folds it out of order"


def test_a_viewer_who_saw_part_of_the_turn_gets_only_what_it_missed() -> None:
    log: dict[str, list[dict[str, Any]]] = {}
    bus = SessionBus(ring=5, backfill=_runlog(log))
    _long_turn(bus, log, "t1", tokens=12)

    # Seen through session frame 4: the opening (1) and tokens w1..w3 (2..4).
    frames = bus.replay("s1", since=4)

    assert all(f["event"] != "turn_started" for f in frames), "an opening the viewer had was sent again"
    assert _text(frames, "t1") == "".join(f"w{n} " for n in range(4, 13)), "missed text or a doubled word"


def test_nothing_is_brought_back_when_the_buffer_dropped_nothing_the_viewer_asked_for() -> None:
    log: dict[str, list[dict[str, Any]]] = {}
    bus = SessionBus(ring=5, backfill=_runlog(log))
    _long_turn(bus, log, "t1", tokens=12)
    last = bus.seq("s1")

    assert [f["session_seq"] for f in bus.replay("s1", since=last - 2)] == [last - 1, last]


def test_a_turn_entirely_gone_from_the_buffer_is_not_brought_back() -> None:
    """It finished; the stored conversation holds it. Bringing back its opening alone would open a
    row that no later frame ever closes."""
    log: dict[str, list[dict[str, Any]]] = {}
    bus = SessionBus(ring=5, backfill=_runlog(log))
    _long_turn(bus, log, "old", tokens=3)
    bus.publish("s1", "done", {"answer": "ok"}, turn_id="old")
    _long_turn(bus, log, "new", tokens=8)

    frames = bus.replay("s1", since=0)

    assert {f["turn_id"] for f in frames} == {"new"}
    assert frames[0]["event"] == "turn_started" and _text(frames, "new").startswith("w1 ")


def test_an_idle_conversations_buffer_is_trimmed_and_its_numbering_goes_on() -> None:
    bus = SessionBus(ring=50)
    bus.publish("idle", "turn_started", {"message": "x"}, turn_id="a")
    bus.publish("busy", "turn_started", {"message": "y"}, turn_id="b")
    time.sleep(0.02)

    bus.trim_idle(max_age=0.01, keep={"busy"})

    assert bus.replay("idle") == [] and bus.replay("busy") != []
    assert bus.publish("idle", "turn_started", {"message": "z"}, turn_id="c")["session_seq"] == 2, (
        "the numbering restarted: a screen holding 1 would skip the next frame"
    )


def test_a_watched_conversation_is_never_trimmed() -> None:
    import asyncio

    bus = SessionBus(ring=50)
    bus.publish("s1", "turn_started", {"message": "x"}, turn_id="a")
    loop = asyncio.new_event_loop()
    try:
        sub = bus.subscribe("s1", name="Ana", loop=loop)
        time.sleep(0.02)
        bus.trim_idle(max_age=0.01, keep=set())
        assert any(f["event"] == "turn_started" for f in bus.replay("s1"))
        bus.unsubscribe("s1", sub.id)
    finally:
        loop.close()


def test_a_deleted_conversations_buffer_is_dropped() -> None:
    bus = SessionBus(ring=50)
    bus.publish("s1", "turn_started", {"message": "x"}, turn_id="a")

    bus.drop("s1")

    assert bus.replay("s1") == [] and bus.seq("s1") == 0


# ------------------------------------------------------------------ the app, end to end


class _Talking:
    entered = threading.Event()
    release = threading.Event()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, on_token: Any = None, **_kw: Any) -> AgentResult:
        if "long" in task and on_token is not None:
            for n in range(1, 41):
                on_token(f"w{n} ")
            type(self).entered.set()
            type(self).release.wait(TIMEOUT)
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


def test_the_app_brings_a_long_turn_back_whole_from_its_run_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api.sharing as sharing
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.setattr(sharing, "RING", 10)
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Talking, raising=True)
    _Talking.entered.clear()
    _Talking.release.clear()
    ws = tmp_path / "ws"
    ws.mkdir()
    app = build_api_app(lambda: ChatSession(_Talking()), workspace=ws,
                        settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"))
    client = TestClient(app)
    turn = threading.Thread(
        target=lambda: client.post("/api/code/turn", json={"message": "a long one", "workspace": str(ws)}),
        daemon=True,
    )
    turn.start()
    try:
        assert _Talking.entered.wait(TIMEOUT)
        [running] = client.get("/api/code/turns/running").json()

        frames = app.state.session_bus.replay(running["session_id"], since=running["live_since"])

        assert frames and frames[0]["event"] == "turn_started", "the opening fell out of the buffer"
        text = _text(frames, running["turn_id"])
        assert text == "".join(f"w{n} " for n in range(1, 41)), f"came back as {text[:40]!r}…"
    finally:
        _Talking.release.set()
        turn.join(TIMEOUT)
