"""Each conversation in the list says what it needs — from facts the server holds, never a guess.

Study 29, P4.1. The sidebar said one thing about a conversation: whether a turn was running in it.
With several conversations working, a person could not tell without opening each one which had
stopped to ask them something, which had failed, and which had changed files they had not looked at.
Every one of those was a fact the server held for a moment and lost when the turn ended.

So each row of `GET /api/code/sessions` now carries ``state``: ``waiting`` (a question of it waits
for the owner — the same join `GET /api/approvals` makes), ``running`` (a turn or a background work
of it), ``failed`` (the last turn raised, or its edits failed the test command), ``review`` (the last
turn edited and the owner's screen has not shown it since) or ``idle``. What has to outlive the turn
is kept in a marks file beside the transcripts (`chimera/core/code_session_marks.py`).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.api.conversation_state import conversation_state
from chimera.core.code_session import CodeSessionStore
from chimera.core.code_session_marks import CodeSessionMarks, ConversationMark
from tests.conversation_fakes import TIMEOUT, Agent, App

# ----------------------------------------------------------------------------- the rule, alone


def test_a_conversation_nothing_happened_in_is_idle() -> None:
    assert conversation_state(running=False, waiting=False, mark=ConversationMark()) == "idle"


def test_a_question_waiting_outranks_the_turn_that_asked_it() -> None:
    # A question only exists while its turn runs; in the other order "waiting" could never show.
    assert conversation_state(running=True, waiting=True, mark=ConversationMark()) == "waiting"
    assert conversation_state(running=True, waiting=False, mark=ConversationMark()) == "running"


def test_a_last_turn_that_failed_reads_failed_and_outranks_unseen_edits() -> None:
    mark = ConversationMark(turns_ended=1, last_failed=True, last_edited=True)
    assert conversation_state(running=False, waiting=False, mark=mark) == "failed"


def test_edits_nobody_looked_at_read_review_until_they_are_seen() -> None:
    mark = ConversationMark(turns_ended=2, last_seen_turn=1, last_edited=True)
    assert conversation_state(running=False, waiting=False, mark=mark) == "review"
    mark.last_seen_turn = 2
    assert conversation_state(running=False, waiting=False, mark=mark) == "idle"


def test_a_turn_that_changed_nothing_is_not_review() -> None:
    mark = ConversationMark(turns_ended=3, last_seen_turn=0, last_edited=False)
    assert conversation_state(running=False, waiting=False, mark=mark) == "idle"


def test_an_old_conversation_reads_failed_only_from_its_stored_verdict() -> None:
    # Older than the marks file: its last receipt's verdict is the one fact it carries.
    empty = ConversationMark()
    assert conversation_state(running=False, waiting=False, mark=empty, last_verdict="failed") == "failed"
    assert conversation_state(running=False, waiting=False, mark=empty, last_verdict="passed") == "idle"
    # Once a turn has ended since, the marks speak for it and an older verdict does not.
    later = ConversationMark(turns_ended=1, last_failed=False)
    assert conversation_state(running=False, waiting=False, mark=later, last_verdict="failed") == "idle"


# ----------------------------------------------------------------------------- the marks file


def test_marks_live_beside_the_transcripts_and_never_list_as_a_conversation(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    (tmp_path / "code_sessions").mkdir()
    store.marks.turn_ended("abc", failed=False, edited=True, at=1.0)

    assert store.marks.path == tmp_path / "code_sessions.marks.json"
    assert store.list_meta() == []


def test_marks_survive_a_new_reader_and_count_every_turn(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.turn_ended("s", failed=True, edited=False, at=1.0)
    marks.turn_ended("s", failed=False, edited=True, at=2.0)

    again = CodeSessionMarks(tmp_path / "m.json").get("s")
    assert (again.turns_ended, again.last_failed, again.last_edited) == (2, False, True)


def test_seeing_a_conversation_writes_only_when_something_was_unseen(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    assert marks.seen("s") is False
    assert not marks.path.exists(), "a screen reporting every draw must not write a file each time"
    marks.turn_ended("s", failed=False, edited=True, at=1.0)
    assert marks.seen("s") is True
    assert marks.seen("s") is False


def test_an_unreadable_marks_file_costs_the_badges_not_the_list(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.path.write_text("{not json", encoding="utf-8")
    assert marks.get("s") == ConversationMark()
    assert marks.all() == {}


def test_deleting_a_conversation_forgets_its_marks(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code_sessions")
    (tmp_path / "code_sessions").mkdir()
    (tmp_path / "code_sessions" / "s1.json").write_text(json.dumps({"session_id": "s1"}), encoding="utf-8")
    store.marks.archive("s1", at=5.0)

    assert store.delete("s1") is True
    assert store.marks.all() == {}


# ----------------------------------------------------------------------------- through the route


@pytest.fixture
def agent() -> Any:
    Agent.hold.clear()
    Agent.held.clear()
    yield Agent
    Agent.hold.set()


def test_a_turn_that_only_talked_leaves_the_conversation_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain the gateway")
    assert app.state(sid) == "idle"


def test_edits_read_review_until_the_screen_shows_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("write the fix")
    assert app.state(sid) == "review"

    # Reading the conversation is not looking at it: the desktop bridge reads too.
    app.client.get(f"/api/code/sessions/{sid}")
    assert app.state(sid) == "review"

    assert app.client.post(f"/api/code/sessions/{sid}/seen").json() == {"changed": True}
    assert app.state(sid) == "idle"
    assert app.client.post(f"/api/code/sessions/{sid}/seen").json() == {"changed": False}


def test_a_turn_that_raised_reads_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    app.turn("boom", session_id=sid)
    assert app.state(sid) == "failed"
    # A later turn that went fine is the last turn now.
    app.turn("explain again", session_id=sid)
    assert app.state(sid) == "idle"


def test_edits_that_failed_the_test_command_read_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    import chimera.api.app
    import chimera.core.verify

    class _Failing:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def verify(self) -> Any:
            return type("Run", (), {"abstained": False, "passed": False, "output": "1 failed"})()

    monkeypatch.setattr(chimera.api.app, "resolve_verify", lambda _r, _ws: ("pytest", "user"))
    monkeypatch.setattr(chimera.core.verify, "CommandVerifier", _Failing)
    app = App(tmp_path, monkeypatch, agent)

    sid = app.turn("write the fix")
    assert app.state(sid) == "failed"


def test_a_running_turn_reads_running_and_its_question_reads_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    thread = threading.Thread(target=lambda: app.turn("hold on", session_id=sid), daemon=True)
    thread.start()
    assert agent.held.wait(TIMEOUT)
    try:
        assert app.state(sid) == "running"
        [turn] = app.client.get("/api/code/turns/running").json()
        app.ask(turn["turn_id"])

        assert app.state(sid) == "waiting"
        # The list and the approvals card count the same question for the same conversation.
        waiting = [r for r in app.rows().values() if r["state"] == "waiting"]
        approvals = app.client.get("/api/approvals").json()
        assert [r["id"] for r in waiting] == [a["session_id"] for a in approvals] == [sid]
    finally:
        agent.hold.set()
        thread.join(TIMEOUT)


def test_a_question_from_a_dead_turn_marks_no_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    app.ask("a-turn-that-is-gone")
    assert app.state(sid) == "idle"
