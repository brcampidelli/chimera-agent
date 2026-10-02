"""An autonomous run waits for its folder the way a coding turn does.

Found reviewing R9 on 2026-09-30 (the review of several conversations at once). Turns took one lock
per folder, so two conversations never edited one folder at once; autonomous runs (`POST /api/runs`)
took none. The app itself kept one run at a time and blocked the composer while a run worked in
the project, but a second window, the MCP bridge or any other client could start a run beside a
turn, or a second run beside the first, in the same folder: two writers, each snapshotting and
reverting a tree the other was changing.

Now turns and runs share one lock per folder. A run whose folder is busy says so and waits; Stop
still reaches it while it waits, and a run stopped there never starts. Different folders never wait.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 15.0


def _sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            out.append((event, json.loads(line.split(":", 1)[1].strip())))
    return out


class _Held:
    """What runs in a folder, for runs and turns alike: it records who is inside and waits."""

    inside: list[str] = []
    release = threading.Event()
    lock = threading.Lock()

    @classmethod
    def work(cls, name: str) -> None:
        with cls.lock:
            cls.inside.append(name)
        cls.release.wait(TIMEOUT)


class _HeldRun:
    def __init__(self, task: str) -> None:
        self.task = task

    def run(self, task: str, thread_id: str | None = None) -> Any:
        _Held.work(task)
        return SimpleNamespace(success=True, answer="ok", attempts=[1], stopped_reason="", paused=False)


class _HeldTurn:
    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:
        _Held.work(task)
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _HeldTurn, raising=True)
    _Held.inside = []
    _Held.release.clear()
    built = build_api_app(
        lambda: ChatSession(_HeldTurn()),
        settings=Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json"),
        solve_agent_factory=lambda req, ws, on_event, settings, stop: _HeldRun(req.task),
    )
    yield built, tmp_path
    _Held.release.set()


def _in_thread(fn: Any) -> tuple[threading.Thread, list[Any]]:
    out: list[Any] = []
    thread = threading.Thread(target=lambda: out.append(fn()), daemon=True)
    thread.start()
    return thread, out


def _folder(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    return folder


def _until(predicate: Any) -> bool:
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_a_second_run_in_the_same_folder_waits_and_says_so(app: Any) -> None:
    built, tmp_path = app
    client = TestClient(built)
    ws = _folder(tmp_path, "ws")
    run = lambda task: client.post("/api/runs", json={"task": task, "workspace": str(ws)})  # noqa: E731
    first, _ = _in_thread(lambda: run("first"))
    assert _until(lambda: _Held.inside == ["first"])
    second, second_out = _in_thread(lambda: run("second"))

    time.sleep(0.5)
    assert _Held.inside == ["first"], "two runs worked in one folder at once"

    _Held.release.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)
    assert _Held.inside == ["first", "second"]
    frames = _sse(second_out[0].text)
    assert any(e == "event" and d.get("kind") == "folder_busy" for e, d in frames), "the wait was silent"
    assert frames[-1][0] == "done" and frames[-1][1]["success"] is True


def test_a_run_and_a_turn_in_the_same_folder_take_turns(app: Any) -> None:
    built, tmp_path = app
    client = TestClient(built)
    ws, other = _folder(tmp_path, "ws"), _folder(tmp_path, "other")
    run, _ = _in_thread(lambda: client.post("/api/runs", json={"task": "the run", "workspace": str(ws)}))
    assert _until(lambda: _Held.inside == ["the run"])
    here, _ = _in_thread(
        lambda: client.post("/api/code/turn", json={"message": "turn here", "workspace": str(ws), "stream": False})
    )
    # The control, started AFTER the one under test: a turn takes a moment to build, and a turn
    # that had not got there yet would read as a turn that waited. This one must get in.
    elsewhere, _ = _in_thread(
        lambda: client.post("/api/code/turn", json={"message": "turn elsewhere", "workspace": str(other), "stream": False})
    )
    assert _until(lambda: any("turn elsewhere" in x for x in _Held.inside)), "the control turn never started"
    time.sleep(0.3)

    assert not any("turn here" in x for x in _Held.inside), "a turn edited the folder a run was working in"

    _Held.release.set()
    for thread in (run, here, elsewhere):
        thread.join(TIMEOUT)
    assert any("turn here" in x for x in _Held.inside), "the turn never ran"


def test_a_run_in_another_folder_starts_at_once(app: Any) -> None:
    built, tmp_path = app
    client = TestClient(built)
    one, other = _folder(tmp_path, "one"), _folder(tmp_path, "other")
    first, _ = _in_thread(lambda: client.post("/api/runs", json={"task": "here", "workspace": str(one)}))
    assert _until(lambda: _Held.inside == ["here"])
    second, _ = _in_thread(lambda: client.post("/api/runs", json={"task": "there", "workspace": str(other)}))

    assert _until(lambda: _Held.inside == ["here", "there"]), "a run waited for a folder it does not use"
    _Held.release.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)


def test_a_run_stopped_while_it_waits_never_starts(app: Any) -> None:
    built, tmp_path = app
    client = TestClient(built)
    ws = _folder(tmp_path, "ws")
    first, _ = _in_thread(lambda: client.post("/api/runs", json={"task": "first", "workspace": str(ws)}))
    assert _until(lambda: _Held.inside == ["first"])
    held = set(built.state.run_cancels)
    second, second_out = _in_thread(lambda: client.post("/api/runs", json={"task": "second", "workspace": str(ws)}))
    assert _until(lambda: len(built.state.run_cancels) == len(held) + 1)
    [waiting] = set(built.state.run_cancels) - held

    assert client.post(f"/api/runs/{waiting}/cancel").json() == {"ok": True}
    second.join(3.0)
    assert not second.is_alive(), "Stop did not reach a run waiting for its folder"
    assert first.is_alive(), "the waiting run ended only because the folder came free"

    _Held.release.set()
    first.join(TIMEOUT)
    assert _Held.inside == ["first"], "a run stopped while waiting started anyway"
    done = _sse(second_out[0].text)[-1]
    assert done[0] == "done" and done[1]["success"] is False and done[1]["stopped_reason"] == "cancelled"
