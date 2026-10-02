"""Undo takes back what its own turn changed, and one conversation at a time edits a folder.

Found reading the code on 2026-09-30 (R2 and R9 of the review of several conversations at once).
Undo restored the whole folder to the snapshot taken before the turn: every file that differed was
written back, and outside a git repository every file created since was deleted. With two
conversations in one folder, undoing A also reverted what B had edited, and whatever the person had
typed since; outside git it deleted B's new files. Nothing kept two turns from editing one folder at
once either, so a snapshot or a verification could describe a mix of both.

Now a turn records what it changed (the difference between the snapshot before it and the folder
right after it, so edits made through the shell count too), Undo puts back only those files, and a
file that changed again after the turn is left as it is and named. Turns in the same folder take
turns, as background works already did; different folders still run at once.
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
from chimera.core.checkpoint import WorkspaceGuard
from chimera.interface import ChatSession

TIMEOUT = 10.0


def _write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


# ----------------------------------------------------------------------------- the checkpoint


def test_undo_restores_the_turns_files_and_leaves_another_conversations_alone(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "a before")
    _write(tmp_path, "b.txt", "b before")
    _write(tmp_path, "gone.txt", "deleted by the turn")
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    # The turn: edits a, creates new, deletes gone.
    _write(tmp_path, "a.txt", "a by the turn")
    _write(tmp_path, "new.txt", "created by the turn")
    (tmp_path / "gone.txt").unlink()
    change = guard.diff_since(before)
    # Another conversation, after the turn: edits b and creates its own file.
    _write(tmp_path, "b.txt", "b by the other conversation")
    _write(tmp_path, "other.txt", "the other conversation's file")

    report = guard.restore_change(change)

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a before"
    assert not (tmp_path / "new.txt").exists(), "a file the turn created goes with it (outside git)"
    assert (tmp_path / "gone.txt").read_text(encoding="utf-8") == "deleted by the turn"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b by the other conversation"
    assert (tmp_path / "other.txt").exists(), "another conversation's new file is not the turn's to delete"
    assert report.kept == []
    assert report.restored == 3


def test_a_file_changed_again_after_the_turn_is_left_as_it_is_and_named(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "a before")
    guard = WorkspaceGuard(tmp_path)
    before = guard.snapshot()
    _write(tmp_path, "a.txt", "a by the turn")
    change = guard.diff_since(before)
    _write(tmp_path, "a.txt", "a typed by the person afterwards")

    report = guard.restore_change(change)

    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a typed by the person afterwards"
    assert report.kept == ["a.txt"]
    assert report.restored == 0


def test_a_turn_that_changed_nothing_has_nothing_to_undo(tmp_path: Path) -> None:
    _write(tmp_path, "a.txt", "same")
    guard = WorkspaceGuard(tmp_path)
    change = guard.diff_since(guard.snapshot())
    assert change.paths == []


# ----------------------------------------------------------------------------- the route


class _Editor:
    """Writes into its folder, reports the edit, and can be held open."""

    hold = threading.Event()
    held = threading.Event()
    entered: list[str] = []

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, on_edit: Any = None, should_stop: Any = None, **_kw: Any) -> AgentResult:
        type(self).entered.append(task)
        folder = Path(re.search(r"\[(.+?)\]", task).group(1)) if "[" in task else None  # type: ignore[union-attr]
        if "hold" in task:
            type(self).held.set()
            deadline = time.monotonic() + TIMEOUT
            while not type(self).hold.is_set() and time.monotonic() < deadline:
                if should_stop is not None and should_stop():
                    break
                time.sleep(0.01)
        if folder is not None and "write" in task:
            (folder / "a.txt").write_text("a by the turn", encoding="utf-8")
            if on_edit is not None:
                on_edit("a.txt", "@@")
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


@pytest.fixture
def editor() -> type[_Editor]:
    _Editor.hold.clear()
    _Editor.held.clear()
    _Editor.entered = []
    yield _Editor
    _Editor.hold.set()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "default-ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(agent()), workspace=ws, settings=settings))


def _turn(client: TestClient, folder: Path, message: str) -> str:
    return client.post(
        "/api/code/turn", json={"message": f"{message} [{folder}]", "workspace": str(folder), "stream": False}
    ).text


def test_undo_through_the_route_leaves_a_file_another_conversation_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editor: type[_Editor]
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    _write(folder, "a.txt", "a before")
    _write(folder, "b.txt", "b before")
    client = _client(tmp_path, monkeypatch, editor)

    frames = _turn(client, folder, "write")
    token = re.search(r'"revert_token": ?"([0-9a-f]+)"', frames).group(1)  # type: ignore[union-attr]
    _write(folder, "b.txt", "b by the other conversation")

    result = client.post(f"/api/code/revert/{token}").json()

    assert result["ok"] is True
    assert (folder / "a.txt").read_text(encoding="utf-8") == "a before"
    assert (folder / "b.txt").read_text(encoding="utf-8") == "b by the other conversation"
    assert result["kept"] == []


def test_two_conversations_in_one_folder_take_turns_and_the_second_is_told_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editor: type[_Editor]
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch, editor)
    replies: dict[str, str] = {}
    first = threading.Thread(target=lambda: replies.setdefault("first", _turn(client, folder, "hold")), daemon=True)
    first.start()
    assert editor.held.wait(TIMEOUT)
    second = threading.Thread(target=lambda: replies.setdefault("second", _turn(client, folder, "read")), daemon=True)
    second.start()
    time.sleep(0.4)

    assert [t.split(" [")[0] for t in editor.entered] == ["hold"], "the second turn ran inside the first one's folder"
    editor.hold.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)
    assert [t.split(" [")[0] for t in editor.entered] == ["hold", "read"]
    assert "folder_busy" in replies["second"], "the waiting turn is told why it waits"


def test_conversations_in_different_folders_still_run_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editor: type[_Editor]
) -> None:
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    two.mkdir()
    client = _client(tmp_path, monkeypatch, editor)
    first = threading.Thread(target=lambda: _turn(client, one, "hold"), daemon=True)
    first.start()
    assert editor.held.wait(TIMEOUT)

    reply = _turn(client, two, "read")

    assert "folder_busy" not in reply
    assert [t.split(" [")[0] for t in editor.entered] == ["hold", "read"]
    editor.hold.set()
    first.join(TIMEOUT)


def test_a_turn_stopped_while_it_waits_for_the_folder_never_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, editor: type[_Editor]
) -> None:
    folder = tmp_path / "proj"
    folder.mkdir()
    client = _client(tmp_path, monkeypatch, editor)
    first = threading.Thread(target=lambda: _turn(client, folder, "hold"), daemon=True)
    first.start()
    assert editor.held.wait(TIMEOUT)
    replies: dict[str, str] = {}
    second = threading.Thread(target=lambda: replies.setdefault("second", _turn(client, folder, "read")), daemon=True)
    second.start()
    deadline = time.monotonic() + TIMEOUT
    waiting: list[dict[str, Any]] = []
    while time.monotonic() < deadline and len(waiting) < 2:
        waiting = client.get("/api/code/turns/running").json()
        time.sleep(0.02)
    waiting_id = [t for t in waiting if "read" in t["message"]][0]["turn_id"]

    assert client.post(f"/api/code/turns/{waiting_id}/stop").status_code == 200
    # Promptly, and while the folder is still taken: a stop that only lands once the other turn lets
    # go of the folder is not a stop that reached a waiting turn.
    second.join(3.0)

    assert not second.is_alive(), "the waiting turn did not stop while it waited"
    assert first.is_alive(), "the first turn must still hold the folder for this to show anything"
    assert [t.split(" [")[0] for t in editor.entered] == ["hold"], "a stopped turn must not start"
    editor.hold.set()
    first.join(TIMEOUT)
