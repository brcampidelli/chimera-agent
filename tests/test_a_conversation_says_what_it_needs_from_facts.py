"""Each conversation in the list says what it needs — from facts the server holds, never a guess.

Study 29, P4.1. The sidebar said one thing about a conversation: whether a turn was running in it.
With several conversations working, a person could not tell without opening each one which had
stopped to ask them something, which had failed, and which had changed files they had not looked at.
Every one of those was a fact the server held for a moment and lost when the turn ended.

So each row of `GET /api/code/sessions` now carries ``state``: ``blocked`` (a question of THIS
conversation's turn waits for the owner), ``running`` (a turn or background work), ``failed`` (the
last turn raised or its edits failed the test command), ``done`` (the owner said so), ``review``
(unseen edits or a PR the agent opened), or ``idle``. Precedence is blocked, running, failed, done,
review, idle (study claude-app, C3). Facts that must outlive the turn are kept beside the
transcripts (`chimera/core/code_session_marks.py`), and the list reads them from ONE file per call.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.api.conversation_state import conversation_state
from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSessionStore
from chimera.core.code_session_marks import CodeSessionMarks, ConversationMark
from tests.conversation_fakes import TIMEOUT, Agent, App

# ----------------------------------------------------------------------------- the rule, alone


PR = {"number": 42, "url": "https://github.com/o/r/pull/42"}


# One row per precedence step: each fact present, together with every fact BELOW it, so the row
# fails if that step is moved under any of them.
@pytest.mark.parametrize(
    ("running", "waiting", "failed", "completed", "unseen", "pr", "expected"),
    [
        (True, True, True, True, True, True, "blocked"),
        (True, False, True, True, True, True, "running"),
        (False, False, True, True, True, True, "failed"),
        (False, False, False, True, True, True, "done"),
        (False, False, False, False, True, True, "review"),
        (False, False, False, False, True, False, "review"),
        (False, False, False, False, False, True, "review"),
        (False, False, False, False, False, False, "idle"),
    ],
)
def test_conversation_state_obeys_its_precedence_table(
    running: bool,
    waiting: bool,
    failed: bool,
    completed: bool,
    unseen: bool,
    pr: bool,
    expected: str,
) -> None:
    mark = ConversationMark(
        turns_ended=1,
        last_seen_turn=0 if unseen else 1,
        last_failed=failed,
        last_edited=unseen,
        pull_requests=[dict(PR)] if pr else [],
        completed=completed,
    )
    assert conversation_state(running=running, waiting=waiting, mark=mark) == expected


def test_a_conversation_nothing_happened_in_is_idle() -> None:
    assert conversation_state(running=False, waiting=False, mark=ConversationMark()) == "idle"


def test_a_question_waiting_outranks_the_turn_that_asked_it() -> None:
    # A question only exists while its turn runs; blocked must take precedence over running.
    assert conversation_state(running=True, waiting=True, mark=ConversationMark()) == "blocked"
    assert conversation_state(running=True, waiting=False, mark=ConversationMark()) == "running"


def test_a_last_turn_that_failed_reads_failed_and_outranks_unseen_edits() -> None:
    mark = ConversationMark(turns_ended=1, last_failed=True, last_edited=True)
    assert conversation_state(running=False, waiting=False, mark=mark) == "failed"


def test_edits_nobody_looked_at_read_review_until_they_are_seen() -> None:
    mark = ConversationMark(turns_ended=2, last_seen_turn=1, last_edited=True)
    assert conversation_state(running=False, waiting=False, mark=mark) == "review"
    mark.last_seen_turn = 2
    assert conversation_state(running=False, waiting=False, mark=mark) == "idle"


def test_a_pull_request_reads_review_until_the_owner_calls_it_done() -> None:
    # A recorded PR never goes away; were review above done, such a conversation could never be done.
    mark = ConversationMark(pull_requests=[dict(PR)])
    assert conversation_state(running=False, waiting=False, mark=mark) == "review"
    mark.completed = True
    assert conversation_state(running=False, waiting=False, mark=mark) == "done"


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

    marks.pull_request_opened("s", number=42, url=PR["url"])
    marks.pull_request_opened("s", number=42, url=PR["url"])  # a retried report counts once
    marks.set_completed("s", completed=True)
    again = CodeSessionMarks(tmp_path / "m.json").get("s")
    assert (again.turns_ended, again.last_failed, again.last_edited) == (2, False, True)
    # The options live on each PR now, both off until the owner turns one on.
    assert again.pull_requests == [{**PR, "fix_on_ci_failure": False, "cleanup_on_merge": False}]
    assert again.completed is True


def test_a_turn_ending_after_done_takes_done_back(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    marks.set_completed("s", completed=True)
    marks.turn_ended("s", failed=False, edited=False, at=1.0)
    assert marks.get("s").completed is False


def test_a_pull_request_record_another_version_wrote_badly_is_not_shown(tmp_path: Path) -> None:
    marks = CodeSessionMarks(tmp_path / "m.json")
    bad = [
        {"number": True, "url": "u1"},  # a bool is an int in Python; it is not PR #1
        {"number": "42", "url": "u2"},
        {"number": 0, "url": "u3"},
        {"number": 7},
        "nonsense",
        {"number": 9, "url": "https://github.com/o/r/pull/9"},
    ]
    marks.path.write_text(
        json.dumps({"sessions": {"s": {"pull_requests": bad, "completed": "yes"}}}), encoding="utf-8"
    )
    mark = marks.get("s")
    assert mark.pull_requests == [
        {
            "number": 9,
            "url": "https://github.com/o/r/pull/9",
            "fix_on_ci_failure": False,
            "cleanup_on_merge": False,
        }
    ]
    assert mark.completed is False
    with pytest.raises(ValueError):
        marks.pull_request_opened("s", number=True, url="x")


@pytest.mark.parametrize(
    ("url", "number"),
    [
        ("https://github.com/o/r/pull/42", 42),
        ("https://github.com/o/r/pull/42/files", 42),
        ("  https://github.com/o/r/pull/7\n", 7),
        ("https://github.com/o/r/pull/7?x=1", 7),
        ("https://github.com/o/r/issues/7", None),
        ("https://github.com/o/r/pull/0", None),
        ("https://github.com/o/r/pull/42abc", None),
        ("", None),
    ],
)
def test_the_pull_request_number_comes_from_its_url(url: str, number: int | None) -> None:
    from chimera.api.code_api import pull_request_number

    assert pull_request_number(url) == number


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

        assert app.state(sid) == "blocked"
        # The list and the approvals card count the same question for the same conversation.
        blocked = [r for r in app.rows().values() if r["state"] == "blocked"]
        approvals = app.client.get("/api/approvals").json()
        assert [r["id"] for r in blocked] == [a["session_id"] for a in approvals] == [sid]
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


def test_a_question_blocks_only_the_conversation_whose_turn_asked_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    other = app.turn("explain the other thing")
    sid = app.turn("explain it")
    thread = threading.Thread(target=lambda: app.turn("hold on", session_id=sid), daemon=True)
    thread.start()
    assert agent.held.wait(TIMEOUT)
    try:
        [turn] = app.client.get("/api/code/turns/running").json()
        app.ask(turn["turn_id"])
        rows = app.rows()
        assert rows[sid]["state"] == "blocked"
        assert rows[other]["state"] == "idle"
    finally:
        agent.hold.set()
        thread.join(TIMEOUT)


# ----------------------------------------------------------------------------- pull requests


def test_a_pull_request_the_turn_opened_is_counted_on_its_own_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    """The wiring end to end: the tool's report reaches the conversation of the TURN that opened it,
    found by the turn's id — the other conversation in the list gets nothing."""
    from chimera.tools.pull_request import OpenPullRequestTool

    built: list[OpenPullRequestTool] = []
    original = OpenPullRequestTool.__init__

    def capture(self: OpenPullRequestTool, *a: Any, **kw: Any) -> None:
        original(self, *a, **kw)
        built.append(self)

    monkeypatch.setattr(OpenPullRequestTool, "__init__", capture)

    class Opener(agent):  # type: ignore[valid-type,misc]
        def run(self, task: str, **kw: Any) -> AgentResult:
            if "open the pr" in task:
                assert built and built[-1].on_opened is not None, "the turn's tool records nothing"
                built[-1].on_opened("https://github.com/o/r/pull/42")
                built[-1].on_opened("https://github.com/o/r/pull/42")
                built[-1].on_opened("https://github.com/o/r/pull/43")
            result: AgentResult = super().run(task, **kw)
            return result

    app = App(tmp_path, monkeypatch, Opener, CHIMERA_PULL_REQUESTS="true")
    other = app.turn("explain it")
    sid = app.turn("open the pr")
    rows = app.rows()
    assert rows[sid]["pull_request"] == {"number": 43, "url": "https://github.com/o/r/pull/43"}
    assert rows[sid]["pull_request_count"] == 2
    assert rows[sid]["state"] == "review"
    assert rows[other]["pull_request"] is None
    assert rows[other]["pull_request_count"] == 0
    assert rows[other]["state"] == "idle"


def test_a_recorder_that_fails_does_not_turn_an_opened_pr_into_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.pull_request as core
    from chimera.tools.pull_request import OpenPullRequestTool

    ready = type(
        "R",
        (),
        {"ready": True, "head": "h", "branch": "b", "base": "main", "remote": "",
         "remote_head": "", "reason": ""},
    )()
    monkeypatch.setattr(core, "readiness", lambda *_a, **_k: ready)
    monkeypatch.setattr(
        core,
        "open_pull_request",
        lambda *_a, **_k: {"ok": True, "url": "https://github.com/o/r/pull/5", "error": ""},
    )
    monkeypatch.setattr(OpenPullRequestTool, "_card", staticmethod(lambda *_a: "card"))
    tool = OpenPullRequestTool(tmp_path, approve=lambda *_a: True)

    def explode(_url: str) -> None:
        raise RuntimeError("disk full")

    tool.on_opened = explode
    assert tool.run(title="t", body="b").startswith("Opened pull request https://github.com/o/r/pull/5")


# ----------------------------------------------------------------------------- the owner's "done"


def test_the_owner_marks_a_conversation_done_and_the_next_turn_takes_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("write the fix")
    assert app.state(sid) == "review"

    response = app.client.post(f"/api/code/sessions/{sid}/done", json={"done": True})
    assert response.json() == {"id": sid, "done": True}
    assert app.state(sid) == "done"

    back = app.client.post(f"/api/code/sessions/{sid}/done", json={"done": False})
    assert back.json()["done"] is False
    assert app.state(sid) == "review"
    app.client.post(f"/api/code/sessions/{sid}/done", json={"done": True})
    app.turn("explain it", session_id=sid)
    assert app.state(sid) == "idle"

    assert app.client.post("/api/code/sessions/nope/done", json={"done": True}).status_code == 404


def test_done_is_refused_while_a_turn_of_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type
) -> None:
    app = App(tmp_path, monkeypatch, agent)
    sid = app.turn("explain it")
    thread = threading.Thread(target=lambda: app.turn("hold on", session_id=sid), daemon=True)
    thread.start()
    assert agent.held.wait(TIMEOUT)
    try:
        response = app.client.post(f"/api/code/sessions/{sid}/done", json={"done": True})
        assert response.status_code == 409
    finally:
        agent.hold.set()
        thread.join(TIMEOUT)


def test_the_bridge_cannot_call_a_conversation_done() -> None:
    # An agent saying its own work is finished is exactly what "done" must not mean.
    from chimera.api.bridge_routes import ROUTES

    assert not [r for r in ROUTES.values() if r.path.endswith("/done")]


# ----------------------------------------------------------------------------- the cost


def _count_opens(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import builtins
    import io

    seen: list[str] = []
    real = io.open

    def counting(file: Any, *a: Any, **kw: Any) -> Any:
        seen.append(str(file))
        return real(file, *a, **kw)

    monkeypatch.setattr(io, "open", counting)
    monkeypatch.setattr(builtins, "open", counting)
    return seen


@pytest.mark.parametrize("n", [3, 30])
def test_the_state_adds_a_constant_number_of_reads_to_the_list_whatever_its_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type, n: int
) -> None:
    """The state must not reopen every conversation, nor read the approvals once per row: beyond
    what `list_meta` reads, one list call opens the marks file once and each waiting question once,
    however many conversations there are. The conversation files themselves are `list_meta`'s
    business (cached by mtime since #923) and are counted apart."""
    app = App(tmp_path, monkeypatch, agent)
    folder = app.home / "code_sessions"
    folder.mkdir(parents=True, exist_ok=True)
    store = CodeSessionStore(folder)
    for i in range(n):
        (folder / f"s{i}.json").write_text(
            json.dumps({"session_id": f"s{i}", "workspace": str(app.folder), "messages": []}),
            encoding="utf-8",
        )
        store.marks.pull_request_opened(
            f"s{i}", number=i + 1, url=f"https://github.com/o/r/pull/{i + 1}"
        )
    app.ask("a-turn-that-is-gone", request_id="q1")
    app.ask("another-gone-turn", request_id="q2")
    app.rows()

    opens = _count_opens(monkeypatch)
    assert len(app.rows()) == n
    outside = [o for o in opens if Path(o).parent != folder]
    assert [o for o in outside if o.endswith("code_sessions.marks.json")] != []
    assert len([o for o in outside if o.endswith("code_sessions.marks.json")]) == 1, outside
    assert len(outside) <= 3, outside
