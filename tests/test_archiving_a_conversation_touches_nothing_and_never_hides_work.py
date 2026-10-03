"""Archiving a conversation moves it out of the list, touches nothing, and never hides work.

Study 29, P4.2. A conversation could only be deleted. Archiving is the other half: a timestamp
beside the transcripts (`chimera/core/code_session_marks.py`), so the transcript, the folder and any
worktree stay exactly as they were, and bringing one back is one click.

`CHIMERA_ARCHIVE_AFTER_DAYS` archives a conversation left alone that long. Empty — the default —
means never. The rule never archives a conversation with a turn running, a question waiting for the
owner, a background work unfinished or a share link open: each would be something still happening,
or someone still looking, hidden in a collapsed section. The person's own archive refuses the first
three for the same reason; a share link they may archive over, since the link keeps working.
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from chimera.api.conversation_state import DAY, archive_refusal, due_for_archive
from chimera.api.works import Work
from chimera.config import Settings
from chimera.core.code_session_marks import ConversationMark
from tests.conversation_fakes import TIMEOUT, Agent, App


@pytest.fixture
def agent() -> Any:
    Agent.hold.clear()
    Agent.held.clear()
    yield Agent
    Agent.hold.set()


def _age(app: App, session_id: str, days: float) -> None:
    """Make a stored conversation look last written ``days`` ago."""
    path = app.home / "code_sessions" / f"{session_id}.json"
    then = time.time() - days * DAY
    os.utime(path, (then, then))


def _digest(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ----------------------------------------------------------------------------- the rule, alone


def test_the_setting_is_never_by_default_and_when_empty_or_unreadable() -> None:
    assert Settings().archive_after_days is None
    assert Settings(CHIMERA_ARCHIVE_AFTER_DAYS="").archive_after_days is None
    assert Settings(CHIMERA_ARCHIVE_AFTER_DAYS="soon").archive_after_days is None
    assert Settings(CHIMERA_ARCHIVE_AFTER_DAYS="30").archive_after_days == 30.0


@pytest.mark.parametrize("after_days", [None, 0.0, -3.0])
def test_no_number_of_days_means_never(after_days: float | None) -> None:
    mark = ConversationMark()
    assert not due_for_archive(updated_at=0.0, mark=mark, now=1e12, after_days=after_days)


def test_a_conversation_is_due_only_after_the_days_have_passed() -> None:
    mark = ConversationMark()
    now = 100 * DAY
    assert due_for_archive(updated_at=now - 8 * DAY, mark=mark, now=now, after_days=7)
    assert not due_for_archive(updated_at=now - 6 * DAY, mark=mark, now=now, after_days=7)


def test_bringing_one_back_restarts_its_clock() -> None:
    now = 100 * DAY
    mark = ConversationMark(unarchived_at=now - DAY)
    assert not due_for_archive(updated_at=now - 30 * DAY, mark=mark, now=now, after_days=7)


@pytest.mark.parametrize(
    "held",
    [
        {"running": True},
        {"waiting": True},
        {"background": True},
        {"shared": True},
    ],
)
def test_each_of_the_four_holds_refuses_the_automatic_archive(held: dict[str, bool]) -> None:
    facts = {"running": False, "waiting": False, "background": False, "shared": False} | held
    assert archive_refusal(**facts)
    assert archive_refusal(running=False, waiting=False, background=False, shared=False) == ""


# ----------------------------------------------------------------------------- by hand


def test_an_archived_conversation_leaves_the_list_and_nothing_on_disk_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("write the fix")
    sessions = app.home / "code_sessions"
    before_folder, before_sessions = _digest(app.folder), _digest(sessions)
    mtime = (sessions / f"{sid}.json").stat().st_mtime

    response = app.client.post(f"/api/code/sessions/{sid}/archive")

    assert response.status_code == 200 and response.json()["archived_at"] is not None
    assert sid not in app.rows()
    assert app.rows(archived=1)[sid]["archived_at"] is not None
    assert _digest(app.folder) == before_folder, "archiving touched the project folder"
    assert _digest(sessions) == before_sessions, "archiving rewrote a transcript"
    assert (sessions / f"{sid}.json").stat().st_mtime == mtime

    back = app.client.post(f"/api/code/sessions/{sid}/unarchive")
    assert back.json() == {"id": sid, "archived_at": None}
    assert sid in app.rows() and sid not in app.rows(archived=1)


def test_archiving_an_unknown_conversation_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    assert app.client.post("/api/code/sessions/nope/archive").status_code == 404
    assert app.client.post("/api/code/sessions/nope/unarchive").status_code == 404


def test_a_conversation_with_a_turn_running_or_a_question_waiting_is_not_archived_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    thread = threading.Thread(target=lambda: app.turn("hold on", session_id=sid), daemon=True)
    thread.start()
    assert agent.held.wait(TIMEOUT)
    try:
        running = app.client.post(f"/api/code/sessions/{sid}/archive")
        assert running.status_code == 409 and "running" in running.json()["detail"]

        [turn] = app.client.get("/api/code/turns/running").json()
        app.ask(turn["turn_id"])
        waiting = app.client.post(f"/api/code/sessions/{sid}/archive")
        assert waiting.status_code == 409 and "waiting" in waiting.json()["detail"]
    finally:
        agent.hold.set()
        thread.join(TIMEOUT)
    assert sid in app.rows()


def test_a_turn_in_an_archived_conversation_brings_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    app.client.post(f"/api/code/sessions/{sid}/archive")
    assert sid not in app.rows()

    app.turn("and the other one", session_id=sid)

    assert app.rows()[sid]["archived_at"] is None


# ----------------------------------------------------------------------------- automatically


def test_without_the_setting_nothing_is_ever_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    _age(app, sid, 3650)
    assert app.rows()[sid]["archived_at"] is None


def test_a_conversation_left_alone_past_the_days_is_archived_on_the_next_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_AFTER_DAYS="7")
    old, recent = app.turn("explain it"), app.turn("explain the other")
    _age(app, old, 8)

    assert set(app.rows()) == {recent}
    assert set(app.rows(archived=1)) == {old}


def test_one_brought_back_is_not_archived_again_on_the_next_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_AFTER_DAYS="7")
    sid = app.turn("explain it")
    _age(app, sid, 30)
    assert sid not in app.rows()

    app.client.post(f"/api/code/sessions/{sid}/unarchive")

    assert sid in app.rows()
    assert sid in app.rows()


def test_the_automatic_archive_leaves_a_conversation_with_a_turn_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_AFTER_DAYS="7")
    sid = app.turn("explain it")
    thread = threading.Thread(target=lambda: app.turn("hold on", session_id=sid), daemon=True)
    thread.start()
    assert agent.held.wait(TIMEOUT)
    try:
        _age(app, sid, 30)
        assert app.rows()[sid]["archived_at"] is None
    finally:
        agent.hold.set()
        thread.join(TIMEOUT)


def _work(app: App, parent: str, *, state: str, turn_id: str = "t-work") -> None:
    app.app.state.work_manager.store.put(
        Work(id="w1", parent=parent, session_id="ws1", turn_id=turn_id, workspace=str(app.folder),
             title="refactor", state=state, created_at=time.time())
    )


def test_the_automatic_archive_leaves_a_conversation_with_a_question_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_AFTER_DAYS="7")
    sid = app.turn("explain it")
    _age(app, sid, 30)
    # A question whose origin is this conversation, through a work that has itself ended — so the
    # question is the only thing holding the conversation.
    _work(app, sid, state="done", turn_id="t-asked")
    app.ask("t-asked")

    row = app.rows()[sid]
    assert row["archived_at"] is None and row["state"] == "waiting"


def test_the_automatic_archive_leaves_a_conversation_with_a_background_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_AFTER_DAYS="7")
    sid = app.turn("explain it")
    _age(app, sid, 30)
    _work(app, sid, state="running")

    row = app.rows()[sid]
    assert row["archived_at"] is None and row["state"] == "running"


def test_the_automatic_archive_leaves_a_shared_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent, CHIMERA_ARCHIVE_AFTER_DAYS="7")
    sid = app.turn("explain it")
    _age(app, sid, 30)
    app.app.state.share_store.mint(sid, label="Ana")

    assert app.rows()[sid]["archived_at"] is None
