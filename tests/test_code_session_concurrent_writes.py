"""A conversation file is never half a file, and no writer's last change is lost.

Seven conversations came up "damaged" with JSON cut off mid-file after a run of twelve parallel
turns (2026-10-27). The store's own save was already atomic; the route that learns a receipt's
route (``code_api._learn_routes``) was not: it read the file, waited on the router, then
``write_text``d the stale snapshot back in place — truncate first, fill after, no lock shared with
the store. Nothing raised, and the truncated file looked like any other corrupt one, so the tests
here drive the writers together and assert on what is on disk.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.core.steplog import StepLog, StepRecord
from chimera.interface import ChatSession
from chimera.providers import generation

SID = "feedfacefeedfacefeedfacefeedface"


class _NoAgent:
    def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - never called
        raise AssertionError("no turn runs in this test")


def _doc(n: int, *, ballast: int = 20000) -> dict[str, Any]:
    # Big enough that a non-atomic write spans several syscalls and a reader can catch it half done.
    return {"session_id": SID, "messages": [{"role": "user", "content": "x" * ballast, "n": n}]}


def _writer(root: str, writer: int, rounds: int) -> None:
    store = CodeSessionStore(Path(root))
    for i in range(rounds):
        # Read-modify-write through the store: each writer appends ITS OWN marker.
        path = store._path(SID)
        with store._guard(path):
            document = store._read_document(path) or _doc(0)
            document.setdefault("receipts", []).append({"writer": writer, "i": i})
            store._write(path, document)


def _markers(store: CodeSessionStore) -> list[tuple[int, int]]:
    receipts = json.loads(store._path(SID).read_text(encoding="utf-8"))["receipts"]
    return sorted((r["writer"], r["i"]) for r in receipts)


def test_threads_never_lose_an_update_or_leave_partial_json(tmp_path: Path) -> None:
    root = tmp_path / "code_sessions"
    store = CodeSessionStore(root)
    store._write(store._path(SID), _doc(0))
    torn: list[str] = []
    stop = threading.Event()

    def reader() -> None:
        path = store._path(SID)
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (FileNotFoundError, PermissionError):
                continue  # Windows: a handle is open while the rename happens; not a torn file
            except ValueError as exc:
                torn.append(str(exc))

    watcher = threading.Thread(target=reader)
    watcher.start()
    writers = [threading.Thread(target=_writer, args=(str(root), w, 15)) for w in range(6)]
    for t in writers:
        t.start()
    for t in writers:
        t.join()
    stop.set()
    watcher.join()

    assert torn == [], f"a reader saw a partial file: {torn[:1]}"
    assert _markers(store) == sorted((w, i) for w in range(6) for i in range(15))


def test_processes_never_lose_an_update_or_leave_partial_json(tmp_path: Path) -> None:
    root = tmp_path / "code_sessions"
    store = CodeSessionStore(root)
    store._write(store._path(SID), _doc(0))
    ctx = multiprocessing.get_context("spawn")
    procs = [ctx.Process(target=_writer, args=(str(root), w, 10)) for w in range(4)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(180)
        assert p.exitcode == 0
    assert _markers(store) == sorted((w, i) for w in range(4) for i in range(10))


def test_a_busy_file_is_retried_never_written_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` refused with PermissionError (a scanner or a reader holds the file, as on
    Windows) must retry the rename and never fall back to writing the live file in place."""
    store = CodeSessionStore(tmp_path / "code_sessions")
    path = store._path(SID)
    store._write(path, _doc(1))
    real = os.replace
    refusals = {"left": 3}
    seen_at_refusal: list[str] = []

    def busy(src: Any, dst: Any) -> None:
        if Path(dst) == path and refusals["left"] > 0:
            refusals["left"] -= 1
            seen_at_refusal.append(path.read_text(encoding="utf-8"))
            raise PermissionError(13, "busy")
        real(src, dst)

    monkeypatch.setattr("chimera.core.filelock.os.replace", busy)
    monkeypatch.setattr("chimera.core.filelock.time.sleep", lambda _s: None)
    store._write(path, _doc(2))

    assert refusals["left"] == 0
    # While the rename was being refused, the live file was still the COMPLETE previous version.
    assert [json.loads(t)["messages"][0]["n"] for t in seen_at_refusal] == [1, 1, 1]
    assert json.loads(path.read_text(encoding="utf-8"))["messages"][0]["n"] == 2
    assert [p.name for p in store.root.iterdir() if p.name.endswith(".tmp")] == []


def test_merging_learned_receipts_keeps_a_turn_saved_meanwhile(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    session = CodeSession(_NoAgent(), session_id=SID)
    session.messages = [{"role": "user", "content": "first"}]
    session.receipts = [{"generation_ids": ["g1"], "model": "openrouter/m", "turn": 1}]
    store.save(session)
    # A second turn is saved while the replay waits on the router for turn 1's route.
    session.messages.append({"role": "assistant", "content": "second turn"})
    session.receipts.append({"generation_ids": ["g2"], "model": "openrouter/m", "turn": 2})
    store.save(session)

    assert store.merge_receipt_fields(SID, {("g1",): {"provider": "Novita", "billed_usd": 0.5}})

    stored = json.loads(store._path(SID).read_text(encoding="utf-8"))
    assert [m["content"] for m in stored["messages"]] == ["first", "second turn"]
    assert [r.get("provider") for r in stored["receipts"]] == ["Novita", None]
    assert stored["receipts"][1]["turn"] == 2


# ------------------------------------------------------------ the route that used to truncate


class _StreamedAgent:
    def run(self, task: str, **_: Any) -> AgentResult:
        log = StepLog()
        log.add(
            StepRecord(
                index=0,
                prompt_tokens=100,
                completion_tokens=5,
                model="openrouter/deepseek/x",
                generation_id="gen-1789611192-turn",
            )
        )
        return AgentResult(
            answer="hello",
            steps=1,
            stopped_reason="final",
            transcript=[
                {"role": "user", "content": task},
                {"role": "assistant", "content": "hello"},
            ],
            model="openrouter/deepseek/x",
            steplog=log,
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _StreamedAgent(), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), OPENROUTER_API_KEY="sk-test")  # type: ignore[arg-type]
    return TestClient(
        build_api_app(lambda: ChatSession(_StreamedAgent()), workspace=ws, settings=settings)
    )


def _session_id(response: Any) -> str:
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: ") and event == "session":
            return str(json.loads(line[len("data: ") :])["session_id"])
    raise AssertionError("no session frame")


def test_reopening_a_conversation_never_rewrites_it_in_place_nor_from_a_stale_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sid = _session_id(client.post("/api/code/turn", json={"message": "oi"}))
    path = tmp_path / "home" / "code_sessions" / f"{sid}.json"

    def lookup(_gen_id: str, **_: Any) -> str:
        # During the router wait another writer saves a new exchange and pins a model.
        document = json.loads(path.read_text(encoding="utf-8"))
        document["messages"].append({"role": "user", "content": "saved while waiting"})
        document["selected_model"] = "pinned/meanwhile"
        store = CodeSessionStore(path.parent)
        with store._guard(path):
            store._write(path, document)
        return "Relace"

    opened_in_place: list[str] = []
    real_open = Path.open

    def spy(self: Path, mode: str = "r", *a: Any, **k: Any) -> Any:
        if self == path and "w" in mode:
            opened_in_place.append(mode)
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(generation, "lookup_route", lookup)
    monkeypatch.setattr(generation, "lookup_cost", lambda *_a, **_k: None)
    monkeypatch.setattr(Path, "open", spy)
    client.get(f"/api/code/sessions/{sid}")

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert opened_in_place == [], "the live conversation file was opened for in-place writing"
    assert any(m.get("content") == "saved while waiting" for m in stored["messages"])
    assert stored["selected_model"] == "pinned/meanwhile"
    assert stored["receipts"][-1]["provider"] == "Relace"
