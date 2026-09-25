"""Background works: a spoken request for work runs on its own while the conversation goes on.

The owner's shape for hands-free use (2026-09-18): the fast model talks, the strong one works, both
at once; several works, in the folders their requests name; stoppable and undoable, from the
screen or by voice. What is pinned: a spoken work request starts a work and the stream says so
without running it in the conversation; the parent's transcript gets one compact exchange; the
work runs on its own session in the works store and its record ends `done` with the answer; one
running work per folder, queued behind it, parallel across folders; stop ends a running work at
its next step and a queued one now; undo restores the files through the receipt's own offer; a
later turn of the conversation carries the works note in its prompt and the three work tools in
its registry, and the news is marked told; a typed request for the same work is an ordinary turn.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.works import Work, WorkManager, WorkStore
from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

# --------------------------------------------------------------------------- the manager alone


def _manager(tmp_path: Path, launched: list[str] | None = None) -> WorkManager:
    published: list[tuple[str, str, dict[str, Any]]] = []
    return WorkManager(
        WorkStore(tmp_path / "works.json"),
        launch=lambda w: (launched if launched is not None else []).append(w.id),
        publish=lambda parent, event, payload: published.append((parent, event, payload)),
        revert=lambda token: {"ok": token == "good", "restored": 2 if token == "good" else 0},
    )


def test_one_running_work_per_folder_queued_behind_it_and_parallel_across_folders(tmp_path: Path) -> None:
    launched: list[str] = []
    manager = _manager(tmp_path, launched)
    a = manager.create(parent="p", workspace=tmp_path / "one", title="fix the login", model="m")
    b = manager.create(parent="p", workspace=tmp_path / "one", title="fix the logout", model="m")
    c = manager.create(parent="p", workspace=tmp_path / "two", title="write the docs", model="m")
    assert (a.state, b.state, c.state) == ("running", "queued", "running")
    assert launched == [a.id, c.id]
    assert (a.number, b.number, c.number) == (1, 2, 3)

    manager.finished(a.id, {"answer": "done.", "stopped_reason": "final", "steps": 3}, revert_token="good", verified="passed")
    assert manager.store.get(a.id).state == "done"  # type: ignore[union-attr]
    assert manager.store.get(b.id).state == "running"  # type: ignore[union-attr]
    assert launched == [a.id, c.id, b.id]


def test_stop_drops_a_queued_work_now_and_flags_a_running_one_for_its_next_step(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    a = manager.create(parent="p", workspace=tmp_path / "one", title="one", model="m")
    b = manager.create(parent="p", workspace=tmp_path / "one", title="two", model="m")
    assert manager.stop(b.id).state == "stopped"  # type: ignore[union-attr]
    assert manager.should_stop(a.id)() is False
    manager.stop(a.id)
    assert manager.should_stop(a.id)() is True
    # The loop ends the run as cancelled; the record says stopped, and the folder frees.
    manager.finished(a.id, {"answer": "Stopped at your request.", "stopped_reason": "cancelled"})
    assert manager.store.get(a.id).state == "stopped"  # type: ignore[union-attr]
    c = manager.create(parent="p", workspace=tmp_path / "one", title="three", model="m")
    assert c.state == "running"


def test_undo_takes_the_receipts_offer_once_and_the_note_tells_the_news_once(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    a = manager.create(parent="p", workspace=tmp_path / "one", title="fix the login", model="m")
    assert manager.undo(a.id)["ok"] is False  # still running
    manager.progress(a.id, tool="read_file")
    manager.progress(a.id, edit="src/login.py")
    manager.finished(a.id, {"answer": "Fixed the login by …", "stopped_reason": "final"}, revert_token="good", verified="none")

    note = manager.note("p")
    assert "fix the login" in note and "done" in note and "NEWS" in note and "src/login.py" in note
    manager.mark_reported("p")
    assert "NEWS" not in manager.note("p")

    result = manager.undo(a.id)
    assert result["ok"] is True and result["restored"] == 2
    assert manager.store.get(a.id).state == "undone"  # type: ignore[union-attr]
    assert manager.undo(a.id)["ok"] is False  # the offer was single-use
    assert manager.note("") == ""


def test_a_reloaded_store_calls_a_work_the_app_died_on_failed(tmp_path: Path) -> None:
    store = WorkStore(tmp_path / "works.json")
    store.put(Work(id="w", parent="p", session_id="s", turn_id="t", workspace=str(tmp_path), title="x", state="running"))
    again = WorkStore(tmp_path / "works.json")
    work = again.get("w")
    assert work is not None and work.state == "failed" and "stopped" in work.error


# --------------------------------------------------------------------------- through the app

SEEN: list[dict[str, Any]] = []
STOPS: list[bool] = []


class _Agent:
    """Answers at once; records the system prompt and the registry it was built with."""

    def __init__(self, *args: Any, **_kw: Any) -> None:
        self.registry = args[1] if len(args) > 1 else None
        self.config = args[2] if len(args) > 2 else None
        if self.config is not None:
            SEEN.append({
                # With the turn notes: the works note travels in the turn context since study 25 wave 2.
                "system_prompt": str(getattr(self.config, "system_prompt", ""))
                + "\n"
                + str(getattr(self.config, "turn_notes", "")),
                "tools": list(self.registry.names()) if self.registry is not None else [],
                "model": getattr(self.config, "model", None),
            })

    def run(self, task: str, **kw: Any) -> AgentResult:
        history = list(kw.get("history") or [])
        return AgentResult(
            answer=f"answered: {task}",
            steps=1,
            stopped_reason="final",
            transcript=[*history, {"role": "user", "content": task}, {"role": "assistant", "content": f"answered: {task}"}],
            model="test/model",
        )


class _EditingAgent(_Agent):
    """Writes a file in the workspace, then answers — so there is something to undo."""

    def run(self, task: str, **kw: Any) -> AgentResult:
        root = Path(str(getattr(self.config, "project_root", "")))
        (root / "login.py").write_text("fixed\n", encoding="utf-8")
        on_edit = kw.get("on_edit")
        if on_edit is not None:
            on_edit("login.py", "--- a/login.py\n+++ b/login.py\n")
        return super().run(task, **kw)


class _SlowAgent(_Agent):
    """Works until told to stop, one step at a time, like the loop does."""

    # Declared by name: the session forwards the stop only to a `run` that names it (a catch-all
    # does not count — `code_session._accepts` says why).
    def run(self, task: str, *, should_stop: Any = None, **kw: Any) -> AgentResult:
        STOPS.append(should_stop is not None)
        deadline = time.time() + 8
        while time.time() < deadline:
            if should_stop is not None and should_stop():
                return AgentResult(answer="Stopped at your request.", steps=2, stopped_reason="cancelled", transcript=[], model="test/model")
            time.sleep(0.02)
        return AgentResult(answer="ran out", steps=99, stopped_reason="final", transcript=[], model="test/model")


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type[_Agent] = _Agent) -> tuple[TestClient, Path]:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "login.py").write_text("broken\n", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    SEEN.clear()
    STOPS.clear()
    return TestClient(build_api_app(lambda: ChatSession(_Agent()), workspace=ws, settings=settings)), ws


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    event, out = "", []
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: "):])))
    return out


def _wait(client: TestClient, session_id: str, work_id: str, states: tuple[str, ...], seconds: float = 6.0) -> dict[str, Any]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        works = client.get(f"/api/code/sessions/{session_id}/works").json()["works"]
        for w in works:
            if w["id"] == work_id and w["state"] in states:
                return dict(w)
        time.sleep(0.05)
    raise AssertionError(f"work {work_id} never reached {states}")


def test_a_spoken_request_for_work_becomes_a_work_and_the_conversation_stays_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, ws = _client(tmp_path, monkeypatch)
    spoken = {"message": "corrija o login", "spoken": True, "thinking": False, "workspace": str(ws)}
    response = client.post("/api/code/turn", json=spoken)
    assert response.status_code == 200
    frames = dict(_frames(response.text))
    assert "work_started" in frames and frames["done"]["stopped_reason"] == "work_started"
    session_id = frames["session"]["session_id"]
    work = frames["work_started"]["work"]
    assert work["parent"] == session_id and work["title"] == "corrija o login" and work["number"] == 1

    # The work ran on its own session, to the end, and its record says what it answered.
    done = _wait(client, session_id, work["id"], ("done",))
    assert done["answer"] == "answered: corrija o login"
    assert done["steps"] == 1 and done["reported"] is False
    # The parent's transcript: one compact exchange, not the work's.
    stored = client.get(f"/api/code/sessions/{session_id}").json()
    assert [e["you"] for e in stored["exchanges"]] == ["corrija o login"]
    assert "started background work 1" in stored["exchanges"][0]["answer"]
    # The parent's bus heard the state changes, and never the work's tokens.
    bus = client.app.state.session_bus  # type: ignore[attr-defined]
    events = [f["event"] for f in bus.replay(session_id, 0)]
    assert "work_state" in events and "turn_started" not in events and "token" not in events
    # The work's own transcript is reachable by its id.
    own = client.get(f"/api/code/works/{work['id']}/session").json()
    assert own["exchanges"] and own["exchanges"][0]["you"] == "corrija o login"

    # The next turn of the conversation is told, and holds the tools — then the news is told.
    client.post("/api/code/turn", json={"message": "como está indo?", "session_id": session_id, "workspace": str(ws)})
    talk = SEEN[-1]
    assert "Background works of this conversation" in talk["system_prompt"] and "NEWS" in talk["system_prompt"]
    assert {"work_status", "work_stop", "work_undo"} <= set(talk["tools"])
    assert _wait(client, session_id, work["id"], ("done",))["reported"] is True

    # A typed request for the same work is an ordinary turn: no work, the answer in the transcript.
    before = len(client.get(f"/api/code/sessions/{session_id}/works").json()["works"])
    client.post("/api/code/turn", json={"message": "corrija o logout", "session_id": session_id, "workspace": str(ws)})
    assert len(client.get(f"/api/code/sessions/{session_id}/works").json()["works"]) == before
    assert "work_status" in SEEN[-1]["tools"]  # the tools ride along on every turn of a conversation with works
    stored = client.get(f"/api/code/sessions/{session_id}").json()
    assert stored["exchanges"][-1]["answer"] == "answered: corrija o logout"


def test_a_running_work_stops_at_its_next_step_from_the_screen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, ws = _client(tmp_path, monkeypatch, _SlowAgent)
    frames = dict(_frames(client.post("/api/code/turn", json={"message": "refatore o login", "spoken": True, "workspace": str(ws)}).text))
    session_id, work = frames["session"]["session_id"], frames["work_started"]["work"]
    running = _wait(client, session_id, work["id"], ("running",))
    assert running["state"] == "running"
    assert client.post(f"/api/code/works/{work['id']}/stop").json()["ok"] is True
    stopped = _wait(client, session_id, work["id"], ("stopped",))
    assert stopped["answer"].startswith("Stopped at your request")
    assert STOPS == [True]  # the loop was handed the stop


def test_a_finished_works_edits_are_undone_through_the_receipts_offer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, ws = _client(tmp_path, monkeypatch, _EditingAgent)
    frames = dict(_frames(client.post("/api/code/turn", json={"message": "corrija o login", "spoken": True, "workspace": str(ws)}).text))
    session_id, work = frames["session"]["session_id"], frames["work_started"]["work"]
    done = _wait(client, session_id, work["id"], ("done",))
    assert done["edits"] == ["login.py"] and done["can_undo"] is True
    assert (ws / "login.py").read_text(encoding="utf-8") == "fixed\n"
    undone = client.post(f"/api/code/works/{work['id']}/undo").json()
    assert undone["ok"] is True and undone["work"]["state"] == "undone"
    assert (ws / "login.py").read_text(encoding="utf-8") == "broken\n"
    assert client.post(f"/api/code/works/{work['id']}/undo").json()["ok"] is False
    assert client.post("/api/code/works/nope/stop").status_code == 404


def test_deleting_the_conversation_forgets_its_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, ws = _client(tmp_path, monkeypatch)
    frames = dict(_frames(client.post("/api/code/turn", json={"message": "corrija o login", "spoken": True, "workspace": str(ws)}).text))
    session_id, work = frames["session"]["session_id"], frames["work_started"]["work"]
    _wait(client, session_id, work["id"], ("done",))
    assert client.delete(f"/api/code/sessions/{session_id}").json()["ok"] is True
    assert client.get(f"/api/code/sessions/{session_id}/works").json()["works"] == []


def test_the_manager_survives_a_launcher_that_raises(tmp_path: Path) -> None:
    def boom(_w: Work) -> None:
        raise RuntimeError("no model")

    manager = WorkManager(
        WorkStore(tmp_path / "works.json"), launch=boom,
        publish=lambda *_a: None, revert=lambda _t: {"ok": False},
    )
    work = manager.create(parent="p", workspace=tmp_path, title="x", model="m")
    assert work.state == "failed" and "no model" in work.error
    # And the folder is free again for the next one.
    nxt = manager.create(parent="p", workspace=tmp_path, title="y", model="m")
    assert nxt.state == "failed"  # launched (and failed) rather than queued behind a corpse


def test_works_are_created_from_several_threads_without_losing_the_queue(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    ids: list[str] = []

    def one(i: int) -> None:
        ids.append(manager.create(parent="p", workspace=tmp_path / "one", title=f"w{i}", model="m").id)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    states = [manager.store.get(i).state for i in ids]  # type: ignore[union-attr]
    assert states.count("running") == 1 and states.count("queued") == 5


def test_a_spoken_sentence_about_the_works_is_talk_not_a_new_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Measured live on 2026-09-18: "pode parar o trabalho um, mudei de ideia" and "desfaz o que
    ele fez" both STARTED a work (the classifier reads "mude" and "faz" as work). A control verb
    beside the word "work", or in a conversation that has works, goes to the talking model, which
    holds the tools for exactly that."""
    client, ws = _client(tmp_path, monkeypatch)
    frames = dict(_frames(client.post("/api/code/turn", json={"message": "corrija o login", "spoken": True, "workspace": str(ws)}).text))
    session_id = frames["session"]["session_id"]
    _wait(client, session_id, frames["work_started"]["work"]["id"], ("done",))

    for sentence in ("pode parar o trabalho um, mudei de ideia", "desfaz o que ele fez", "como está o andamento?"):
        by = dict(_frames(client.post("/api/code/turn", json={"message": sentence, "spoken": True, "session_id": session_id, "workspace": str(ws)}).text))
        assert "work_started" not in by, sentence
        assert by["done"]["answer"] == f"answered: {sentence}", sentence
        assert "work_stop" in SEEN[-1]["tools"], sentence
    assert len(client.get(f"/api/code/sessions/{session_id}/works").json()["works"]) == 1
    # Without any work, and without the word, a control verb is nothing special.
    fresh = dict(_frames(client.post("/api/code/turn", json={"message": "desfaz o login e crie de novo", "spoken": True, "workspace": str(ws)}).text))
    assert "work_started" in fresh
