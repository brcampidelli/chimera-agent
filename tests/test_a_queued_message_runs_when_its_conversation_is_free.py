"""A message queued on a busy Code conversation runs when the conversation is free, once, as sent.

Pinned through the ENDPOINT, not the queue class: the defects a review of S5 found (2026-10-16)
lived in the seams between the inbox and the turn machinery — a wake-up lost while another drain
held the lock, a terminal or bridge message run as a GUEST because its label was read as an
identity, a refused start that dropped the message, a stream that never ended when the queue's
bookkeeping raised, and a queued turn that could start in a conversation that had been deleted.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.code_session_queue import CodeTurnQueue
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 15.0


class _Held:
    """A fake agent: a task containing "hold" waits for ``release``, one containing "gate" for
    ``gate``; every run is recorded."""

    release = threading.Event()
    gate = threading.Event()
    guard = threading.Lock()
    ran: list[str] = []
    running = 0
    most_at_once = 0
    tools: dict[str, list[str]] = {}

    def __init__(self, *args: Any, **_kw: Any) -> None:
        registry = args[1] if len(args) > 1 else None
        self.names = sorted(registry.names()) if registry is not None and hasattr(registry, "names") else []

    def run(self, task: str, **kw: Any) -> AgentResult:
        cls = type(self)
        with cls.guard:
            cls.running += 1
            cls.most_at_once = max(cls.most_at_once, cls.running)
            cls.tools[task] = self.names
        try:
            if "hold" in task:
                cls.release.wait(TIMEOUT)
            if "gate" in task:
                cls.gate.wait(TIMEOUT)
        finally:
            with cls.guard:
                cls.running -= 1
                cls.ran.append(task)
        history = list(kw.get("history") or [])
        return AgentResult(
            answer="ok", steps=1, stopped_reason="final",
            transcript=[*history, {"role": "user", "content": task}, {"role": "assistant", "content": "ok"}],
        )


def _reset() -> None:
    _Held.release.clear()
    _Held.gate.clear()
    _Held.ran = []
    _Held.running = 0
    _Held.most_at_once = 0
    _Held.tools = {}


def _build(home: Path, ws: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Held, raising=True)
    app = build_api_app(lambda: ChatSession(_Held()), workspace=ws,
                        settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"))
    return TestClient(app)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    _reset()
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    ws.mkdir()
    yield _build(home, ws, monkeypatch), ws, home, monkeypatch
    _Held.release.set()
    _Held.gate.set()


def _until(predicate: Any) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def _events(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and event:
            out.append((event, json.loads(line.split(":", 1)[1].strip())))
    return out


def _open(http: TestClient, ws: Path) -> str:
    r = http.post("/api/code/turn", json={"message": "hello", "workspace": str(ws)})
    return str(dict(_events(r.text))["session"]["session_id"])


def _hold(http: TestClient, session_id: str, message: str = "hold") -> threading.Thread:
    thread = threading.Thread(
        target=lambda: http.post("/api/code/turn", json={"message": message, "session_id": session_id}),
        daemon=True,
    )
    thread.start()
    return thread


def _queue(http: TestClient, session_id: str, client_id: str, message: str, **extra: Any) -> Any:
    return http.post("/api/code/turn", json={
        "message": message, "session_id": session_id, "mode": "queue", "client_id": client_id, **extra,
    })


def _items(http: TestClient, session_id: str) -> list[dict[str, Any]]:
    r = http.get(f"/api/code/sessions/{session_id}/queue")
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


def _queued(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    frames = dict(_events(response.text))
    assert "queued" in frames, response.text
    return frames["queued"]


def test_busy_conversation_queues_in_order_and_runs_one_at_a_time(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    holder = _hold(http, sid)
    assert _until(lambda: _Held.running == 1)

    first = _queued(_queue(http, sid, "c1", "queued one"))
    second = _queued(_queue(http, sid, "c2", "queued two"))
    again = _queued(_queue(http, sid, "c1", "queued one"))
    assert (first["position"], second["position"]) == (1, 2)
    assert again["id"] == first["id"], "the same client id made a second item"

    _Held.release.set()
    holder.join(TIMEOUT)
    assert _until(lambda: [i["state"] for i in _items(http, sid)] == ["done", "done"])
    assert _Held.ran[-2:] == ["queued one", "queued two"]
    assert _Held.ran.count("queued one") == 1
    assert _Held.most_at_once == 1, "two turns of one conversation ran at once"


def test_an_idle_conversation_runs_a_queued_message_now_and_a_retry_does_not_rerun_it(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    r = _queue(http, sid, "now", "run me")
    events = [name for name, _ in _events(r.text)]
    assert "session" in events and "done" in events, "an idle conversation answered queued"
    assert [i["state"] for i in _items(http, sid)] == ["done"]

    retry = _queued(_queue(http, sid, "now", "run me"))
    assert retry["state"] == "done"
    assert _Held.ran.count("run me") == 1


def test_the_posture_stored_with_the_item_is_the_one_applied(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    holder = _hold(http, sid, "hold with full reach")
    assert _until(lambda: _Held.running == 1)
    _queued(_queue(http, sid, "ro", "queued read only",
                        posture={"reach": "read_only", "approval": "suspicious"}))
    _Held.release.set()
    holder.join(TIMEOUT)
    assert _until(lambda: "queued read only" in _Held.tools and "queued read only" in _Held.ran)
    held_tools = set(_Held.tools["hold with full reach"])
    queued_tools = set(_Held.tools["queued read only"])
    assert "write_file" in held_tools
    assert "write_file" not in queued_tools, "the queued turn borrowed the previous turn's posture"


@pytest.mark.parametrize("source", ["terminal", "bridge"])
def test_a_terminal_or_bridge_message_runs_as_the_owner(env: Any, source: str) -> None:
    http, ws, _home, monkeypatch = env
    import chimera.integrations.openapi_store as connectors

    owner_turns: list[int] = []
    real = connectors.with_connectors

    def spy(*args: Any, **kwargs: Any) -> Any:
        owner_turns.append(1)  # called only on a turn that is not a guest's (assemble_registry)
        return real(*args, **kwargs)

    monkeypatch.setattr(connectors, "with_connectors", spy)
    sid = _open(http, ws)
    # Busy first, so the message goes through the queue's drain and not the idle fast path.
    holder = _hold(http, sid)
    assert _until(lambda: _Held.running == 1)
    before = len(owner_turns)
    # The body tries to claim an identity; it is ignored, and ``source`` is a label.
    _queued(_queue(http, sid, f"{source}-1", "from another screen", source=source, author="Mallory"))
    _Held.release.set()
    holder.join(TIMEOUT)
    assert _until(lambda: [i["state"] for i in _items(http, sid)] == ["done"])
    assert len(owner_turns) == before + 1, f"a {source} message ran as a guest"
    assert _items(http, sid)[0]["source"] == source


def test_a_body_cannot_declare_itself_a_guest(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    r = _queue(http, sid, "g", "pretend", source="guest")
    assert r.status_code == 422, r.text


def test_two_turns_ending_together_start_the_queued_message_once(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    first = _hold(http, sid, "hold one")
    assert _until(lambda: _Held.running == 1)
    second = _hold(http, sid, "hold two")  # admitted, waits on the conversation's lock
    time.sleep(0.3)
    _queued(_queue(http, sid, "after", "after both"))
    _Held.release.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)
    assert _until(lambda: [i["state"] for i in _items(http, sid)] == ["done"])
    time.sleep(0.3)
    assert _Held.ran.count("after both") == 1
    assert _Held.ran.index("after both") > _Held.ran.index("hold two")
    assert _Held.most_at_once == 1


def test_a_message_queued_while_a_queued_one_runs_waits_its_turn(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    holder = _hold(http, sid)
    assert _until(lambda: _Held.running == 1)
    draining = _queued(_queue(http, sid, "q1", "gate during drain"))
    _Held.release.set()
    holder.join(TIMEOUT)
    # The drain started the queued item; the conversation is busy with it now.
    assert _until(lambda: _Held.running == 1 and _items(http, sid)[0]["state"] == "running")
    assert _items(http, sid)[0]["id"] == draining["id"]
    third = _queued(_queue(http, sid, "q2", "after the drained one"))
    assert third["position"] == 1
    _Held.gate.set()
    assert _until(lambda: [i["state"] for i in _items(http, sid)] == ["done", "done"])
    assert _Held.ran[-2:] == ["gate during drain", "after the drained one"]
    assert _Held.most_at_once == 1


def test_a_starting_item_cannot_be_cancelled_and_a_waiting_one_never_runs(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    holder = _hold(http, sid)
    assert _until(lambda: _Held.running == 1)
    starting = _queued(_queue(http, sid, "s", "gate starting"))
    waiting = _queued(_queue(http, sid, "w", "never"))
    _Held.release.set()
    holder.join(TIMEOUT)
    assert _until(lambda: _Held.running == 1)  # "gate starting" is the running queued turn

    refused = http.delete(f"/api/code/sessions/{sid}/queue/{starting['id']}")
    assert refused.status_code == 409, refused.text
    cancelled = http.delete(f"/api/code/sessions/{sid}/queue/{waiting['id']}")
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    _Held.gate.set()
    assert _until(lambda: [i["state"] for i in _items(http, sid)] == ["done", "cancelled"])
    time.sleep(0.3)
    assert "never" not in _Held.ran
    missing = http.delete(f"/api/code/sessions/{sid}/queue/nope")
    assert missing.status_code == 404


def test_refusals_happen_at_enqueue_time(env: Any) -> None:
    http, ws, _home, _ = env
    sid = _open(http, ws)
    assert _queue(http, "no-such-conversation", "x", "hi").status_code == 404
    assert http.get("/api/code/sessions/no-such-conversation/queue").status_code == 404
    holder = _hold(http, sid)
    assert _until(lambda: _Held.running == 1)
    # While busy: refused now, not stored and failed later where nobody sees it.
    gone = _queue(http, sid, "x", "hi", workspace=str(ws / "missing"))
    assert gone.status_code == 400, gone.text
    assert _items(http, sid) == []
    for index in range(20):
        _queued(_queue(http, sid, f"fill-{index}", f"fill {index}"))
    full = _queue(http, sid, "one-too-many", "x")
    assert full.status_code == 429, full.text
    _Held.release.set()
    holder.join(TIMEOUT)


def test_a_deleted_conversation_takes_its_queue_with_it(env: Any) -> None:
    http, ws, home, _ = env
    sid = _open(http, ws)
    holder = _hold(http, sid)
    assert _until(lambda: _Held.running == 1)
    _queued(_queue(http, sid, "orphan", "must not run"))
    assert http.delete(f"/api/code/sessions/{sid}").status_code == 200
    _Held.release.set()
    holder.join(TIMEOUT)
    time.sleep(0.5)
    assert "must not run" not in _Held.ran
    assert not (home / "code_sessions" / f"{sid}.queue.jsonl").exists()
    assert http.get(f"/api/code/sessions/{sid}/queue").status_code == 404


def test_deleting_a_project_forgets_its_conversations_queues(env: Any) -> None:
    http, ws, home, _ = env
    sid = _open(http, ws)
    CodeTurnQueue(home / "code_sessions").enqueue(sid, "x", {"message": "never"})
    r = http.delete("/api/code/projects", params={"workspace": str(ws)})
    assert r.status_code == 200, r.text
    assert not (home / "code_sessions" / f"{sid}.queue.jsonl").exists()


def test_listing_the_queue_starts_nothing(env: Any) -> None:
    http, ws, home, _ = env
    sid = _open(http, ws)
    CodeTurnQueue(home / "code_sessions").enqueue(sid, "side", {"message": "side effect"})
    assert [i["state"] for i in _items(http, sid)] == ["pending"]
    http.get("/api/code/sessions")
    time.sleep(0.3)
    assert "side effect" not in _Held.ran


def test_restart_runs_waiting_items_and_announces_the_interrupted_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset()
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    http = _build(home, ws, monkeypatch)
    sid = _open(http, ws)
    queue = CodeTurnQueue(home / "code_sessions")
    crashed, _ = queue.enqueue(sid, "crashed", {"message": "was running", "session_id": sid})
    waiting, _ = queue.enqueue(sid, "waiting", {"message": "was waiting", "session_id": sid})
    assert queue.claim_next(sid) is not None  # the process "died" with this one running

    restarted = _build(home, ws, monkeypatch)
    assert _until(lambda: "was waiting" in _Held.ran)
    states = {i["id"]: i["state"] for i in _items(restarted, sid)}
    assert states[crashed.id] == "interrupted" and _Held.ran.count("was running") == 0
    assert _until(lambda: {i["id"]: i["state"] for i in _items(restarted, sid)}[waiting.id] == "done")
    frames = restarted.app.state.session_bus.replay(sid)
    notices = [f for f in frames if f["event"] == "notice"]
    assert [f["payload"]["code"] for f in notices if f["payload"].get("queue_item_id") == crashed.id] == [
        "queue_item_interrupted"
    ], frames


def test_a_full_conversation_gives_the_message_back_instead_of_failing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.api.code_api as code_api

    _reset()
    home, ws = tmp_path / "home", tmp_path / "ws"
    ws.mkdir()
    http = _build(home, ws, monkeypatch)
    sid = _open(http, ws)
    queue = CodeTurnQueue(home / "code_sessions")
    item, _ = queue.enqueue(sid, "x", {"message": "wait for room", "session_id": sid})

    monkeypatch.setattr(code_api, "MAX_TURNS_PER_CONVERSATION", 0)  # every admission is a 429
    restarted = _build(home, ws, monkeypatch)  # the startup drain meets the refusal
    time.sleep(0.3)
    assert [i["state"] for i in _items(restarted, sid)] == ["pending"], "the message was dropped"
    idle = _queue(restarted, sid, "y", "also waits")  # the idle path meets it too
    assert _queued(idle)["state"] == "pending"

    monkeypatch.setattr(code_api, "MAX_TURNS_PER_CONVERSATION", 4)
    restarted.post("/api/code/turn", json={"message": "hello", "session_id": sid})
    assert _until(lambda: [i["state"] for i in _items(restarted, sid)] == ["done", "done"])
    assert _Held.ran.count("wait for room") == 1
    assert item.id == _items(restarted, sid)[0]["id"]


def test_the_stream_ends_even_when_the_queue_bookkeeping_fails(
    env: Any,
) -> None:
    http, ws, _home, monkeypatch = env
    sid = _open(http, ws)

    def broken(self: CodeTurnQueue, session_id: str, item_id: str) -> bool:
        raise OSError("disk full")

    monkeypatch.setattr(CodeTurnQueue, "finish", broken)
    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(_queue(http, sid, "z", "finishes").status_code), daemon=True
    )
    thread.start()
    thread.join(TIMEOUT)
    assert result == [200], "the stream never ended"
