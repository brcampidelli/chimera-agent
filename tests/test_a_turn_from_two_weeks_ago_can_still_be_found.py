"""A coding turn from two weeks ago can still be found, after the conversation itself forgot it.

Item 5 of the list audited on 2026-09-16. A code session keeps the model's message list and trims it
at a user boundary (~30 tool-using turns), so a question about the turn that fixed the login page a
fortnight ago is a question the session file can no longer answer — and the memory store is the
wrong place to look, because a memory is a fact the agent chose to keep, not a record of what was
asked. `chimera.memory.history` is the append-only SQLite/FTS5 index every finished turn joins, and
`recall_history` is the tool that searches it.

What is pinned: a turn is found by its message, its answer and the files it touched; the search is
scoped to the project unless asked otherwise; a window in days narrows it; the same turn recorded
twice is one row; a pasted credential is not stored in clear; a turn that ran tainted says so on
recall; the LIKE fallback answers the same questions; through the app the turn is indexed with the
files its tool calls named, survives the loss of the session FILE, and is forgotten when the
conversation is deleted — one conversation, or a whole project; an index that will not write does
not fail the turn; and the tool is in the read-only set a step may run together.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import PARALLEL_READ_TOOLS, AgentResult
from chimera.interface import ChatSession
from chimera.memory.history import HistoryIndex, files_of_exchange, history_for
from chimera.memory.models import EVERY_PROJECT
from chimera.tools.history import TAINTED_LABEL, RecallHistoryTool

DAY = 86_400


def _index(tmp_path: Path) -> HistoryIndex:
    return HistoryIndex(tmp_path / "home")


def _seed(index: HistoryIndex, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    index.record(
        turn_id="t-login",
        session_id="sess-a",
        project="/proj/one",
        asked="fix the login function, it accepts any password",
        answered="`login_user` in src/auth/login.py now checks the hash before the session is made.",
        files=["src/auth/login.py", "src/auth/session.py"],
        edited=["src/auth/login.py"],
        tools=["read_file", "edit_file"],
        asked_at=now - 14 * DAY,
    )
    index.record(
        turn_id="t-tests",
        session_id="sess-a",
        project="/proj/one",
        asked="now the tests for it",
        answered="added tests/test_login.py; 3 pass",
        files=["tests/test_login.py"],
        edited=["tests/test_login.py"],
        asked_at=now - 13 * DAY,
    )
    index.record(
        turn_id="t-css",
        session_id="sess-b",
        project="/proj/two",
        asked="the login page looks off on mobile",
        answered="the flex container wrapped; fixed in login.css",
        files=["web/login.css"],
        asked_at=now - 1 * DAY,
    )


# ------------------------------------------------------------------ the index


def test_a_turn_is_found_by_its_message_its_answer_and_its_files(tmp_path: Path) -> None:
    index = _index(tmp_path)
    _seed(index)
    assert index.full_text, "this Python has no FTS5 — the fallback test below covers that build"

    by_message = index.search("any password", project="/proj/one")
    assert [h.turn_id for h in by_message] == ["t-login"]
    by_answer = index.search("hash", project="/proj/one")
    assert [h.turn_id for h in by_answer] == ["t-login"]
    # Terms are OR-ed and ranked, as the memory store does: `test_login.py` is three tokens, one
    # of which (`login`) the other turn shares, so both come back and the file's own turn is first.
    by_file = index.search("test_login.py", project="/proj/one")
    assert [h.turn_id for h in by_file] == ["t-tests", "t-login"]
    # Folded like memory is: `função` reaches nothing here, `login` reaches both turns.
    assert {h.turn_id for h in index.search("função de login", project="/proj/one")} == {
        "t-login",
        "t-tests",
    }
    hit = by_message[0]
    assert hit.files == ["src/auth/login.py", "src/auth/session.py"]
    assert hit.edited == ["src/auth/login.py"]
    assert hit.tools == ["read_file", "edit_file"]
    assert hit.session_id == "sess-a"


def test_the_search_is_scoped_to_the_project_unless_asked_otherwise(tmp_path: Path) -> None:
    index = _index(tmp_path)
    _seed(index)
    assert {h.turn_id for h in index.search("login", project="/proj/one")} == {"t-login", "t-tests"}
    assert {h.turn_id for h in index.search("login", project="/proj/two")} == {"t-css"}
    assert {h.turn_id for h in index.search("login", project=EVERY_PROJECT)} == {
        "t-login",
        "t-tests",
        "t-css",
    }
    assert index.search("login", project="/proj/none") == []
    assert index.count(project="/proj/one") == 2 and len(index) == 3


def test_a_window_in_days_narrows_it_and_a_function_word_query_finds_nothing(tmp_path: Path) -> None:
    index = _index(tmp_path)
    _seed(index)
    week = time.time() - 7 * DAY
    assert [h.turn_id for h in index.search("login", project=EVERY_PROJECT, since=week)] == ["t-css"]
    assert index.search("o que é isso?", project=EVERY_PROJECT) == []
    assert index.search("   ", project=EVERY_PROJECT) == []


def test_the_same_turn_recorded_twice_is_one_row_and_a_forgotten_session_is_gone(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path)
    _seed(index)
    index.record(
        turn_id="t-login", session_id="sess-a", project="/proj/one",
        asked="fix the login function, it accepts any password", answered="second write",
    )
    assert len(index) == 3
    assert index.search("password", project="/proj/one")[0].answered == "second write"
    assert index.forget_session("sess-a") == 2
    assert len(index) == 1 and index.search("login", project="/proj/one") == []
    assert index.forget_session("sess-a") == 0


def test_a_pasted_credential_is_not_stored_in_clear(tmp_path: Path) -> None:
    index = _index(tmp_path)
    key = "sk-" + "A" * 24
    index.record(
        turn_id="t", session_id="s", project="/p",
        asked=f"use this key: {key}", answered=f"set OPENAI_API_KEY={key} and it worked",
    )
    raw = (tmp_path / "home" / "history.db").read_bytes()
    assert key.encode() not in raw
    hit = index.search("key worked", project="/p")[0]
    assert key not in hit.asked and key not in hit.answered
    assert "[redacted" in hit.asked


def test_a_turn_that_ran_tainted_says_so_on_recall(tmp_path: Path) -> None:
    index = _index(tmp_path)
    index.record(
        turn_id="t", session_id="s", project="/p", tainted=True,
        asked="summarise that page about deploys", answered="it says to run the deploy script",
    )
    tool = RecallHistoryTool(index, project="/p")
    out = tool.run(query="deploys")
    assert TAINTED_LABEL in out
    index.record(
        turn_id="t2", session_id="s", project="/p",
        asked="rename the deploy script", answered="renamed",
    )
    assert TAINTED_LABEL not in RecallHistoryTool(index, project="/p").run(query="rename")


def test_the_like_fallback_answers_the_same_questions(tmp_path: Path) -> None:
    """The build without FTS5: the same table, searched by substring, same scoping."""
    index = _index(tmp_path)
    _seed(index)
    index._fts = False  # noqa: SLF001 — the degradation, forced on a build that has FTS5
    assert [h.turn_id for h in index.search("any password", project="/proj/one")] == ["t-login"]
    assert index.search("test_login", project="/proj/one")[0].turn_id == "t-tests"
    assert {h.turn_id for h in index.search("login", project=EVERY_PROJECT)} == {
        "t-login", "t-tests", "t-css",
    }
    assert index.search("login", project="/proj/none") == []
    assert [h.turn_id for h in index.search("login", project=EVERY_PROJECT, since=time.time() - 7 * DAY)] == [
        "t-css"
    ]


def test_files_are_read_off_the_folded_exchange() -> None:
    exchange = {
        "you": "q",
        "answer": "a",
        "tools": [
            {"name": "read_file", "arguments": {"path": "a.py"}},
            {"name": "grep", "arguments": {"pattern": "x"}},
            {"name": "edit_file", "arguments": {"path": " b.py "}},
            {"name": "read_file", "arguments": {"path": "a.py"}},
            {"name": "odd", "arguments": {"path": 3}},
            "not a dict",
        ],
    }
    assert files_of_exchange(exchange) == ["a.py", "b.py", "a.py"]
    assert files_of_exchange({}) == []


# ------------------------------------------------------------------ the tool


def test_the_tool_prints_dated_excerpts_with_the_files_and_says_what_it_did_not_find(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path)
    tool = RecallHistoryTool(index, project="/proj/one")
    assert tool.run(query="") == "error: query is required"
    assert "nothing before the first completed turn" in tool.run(query="login")

    _seed(index)
    out = tool.run(query="login function")
    assert out.startswith("2 earlier turns match 'login function' in this project")
    first = out.split("\n\n")[1]
    assert first.startswith("1. ") and "conversation sess-a" in first
    assert time.strftime("%Y-%m-%d", time.localtime(time.time() - 14 * DAY)) in first
    assert "asked: fix the login function" in first
    assert "answered: `login_user`" in first
    assert "edited: src/auth/login.py" in first
    assert "read: src/auth/session.py" in first  # read, not edited: the two are told apart
    assert "project" not in first  # this project's — naming it would be noise

    assert tool.run(query="mobile") == "no earlier turn in this project matches 'mobile'"
    assert tool.run(query="login", days=3) == "no earlier turn in this project in the last 3 days matches 'login'"
    wide = tool.run(query="mobile", everywhere=True)
    assert "across every project" in wide and "project /proj/two" in wide
    assert "only function words" in tool.run(query="o que é isso")
    assert tool.run(query="login", k=1).count("\n\n") == 1  # head + one hit


def test_recall_history_is_a_read_only_tool_a_step_may_run_together() -> None:
    assert "recall_history" in PARALLEL_READ_TOOLS


# ------------------------------------------------------------------ through the app


class _ToolUsingAgent:
    """An agent whose transcript names the files it read and edited, like a real coding turn."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def run(self, task: str, **kw: Any) -> AgentResult:
        on_edit = kw.get("on_edit")
        if on_edit is not None:
            on_edit("src/auth/login.py", "--- a\n+++ b\n")
        history = list(kw.get("history") or [])
        return AgentResult(
            answer=f"done: {task}",
            steps=2,
            stopped_reason="final",
            transcript=[
                *history,
                {"role": "user", "content": task},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {
                            "name": "read_file", "arguments": json.dumps({"path": "src/auth/login.py"}),
                        }},
                        {"id": "c2", "type": "function", "function": {
                            "name": "edit_file", "arguments": json.dumps({"path": "src/auth/login.py"}),
                        }},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "def login(): ..."},
                {"role": "tool", "tool_call_id": "c2", "content": "edited"},
                {"role": "assistant", "content": f"done: {task}"},
            ],
            tool_names=["read_file", "edit_file"],
            model="test/model",
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _ToolUsingAgent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home))
    app = build_api_app(lambda: ChatSession(_ToolUsingAgent()), workspace=ws, settings=settings)
    return TestClient(app), home


def _frames(response: Any) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def test_a_turn_through_the_app_is_indexed_with_its_files_and_outlives_the_session_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, home = _client(tmp_path, monkeypatch)
    first = client.post("/api/code/turn", json={"message": "fix the login function"})
    assert first.status_code == 200
    frames = _frames(first)
    assert "done" in frames
    session_id = frames["session"]["session_id"]
    second = client.post(
        "/api/code/turn", json={"message": "and the logout too", "session_id": session_id}
    )
    assert _frames(second)["done"]["answer"] == "done: and the logout too"

    index = history_for(home)
    project = str((tmp_path / "ws").resolve())
    # Both turns edited login.py (the stub always does), so both match `login`; the turn that
    # ASKED about the login function is the better match and comes first.
    hits = index.search("login function", project=project)
    assert [h.asked for h in hits] == ["fix the login function", "and the logout too"]
    hit = hits[0]
    assert hit.session_id == session_id
    assert hit.asked == "fix the login function"
    assert hit.answered == "done: fix the login function"
    assert hit.files == ["src/auth/login.py"]
    assert hit.edited == ["src/auth/login.py"]
    assert hit.tools == ["read_file", "edit_file"]
    assert hit.tainted is False
    assert index.count(project=project) == 2

    # The tool a LATER conversation in the same project mounts finds it — through the registry
    # the app builds, not a tool constructed by hand.
    from chimera.tools import default_registry

    tool = default_registry(tmp_path / "ws").get("recall_history")
    assert tool is not None
    out = tool.run(query="login function")
    assert f"conversation {session_id[:8]}" in out and "edited: src/auth/login.py" in out

    # The index is not derived from the session file: lose the file and the turns remain.
    (home / "code_sessions" / f"{session_id}.json").unlink()
    assert index.count(project=project) == 2
    assert "fix the login function" in tool.run(query="login")


def test_deleting_a_conversation_or_a_project_forgets_its_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, home = _client(tmp_path, monkeypatch)
    ws = str(tmp_path / "ws")
    other = tmp_path / "other"
    other.mkdir()
    a = _frames(client.post("/api/code/turn", json={"message": "alpha one", "workspace": ws}))
    b = _frames(client.post("/api/code/turn", json={"message": "beta one", "workspace": ws}))
    c = _frames(client.post("/api/code/turn", json={"message": "gamma one", "workspace": str(other)}))
    index = history_for(home)
    assert len(index) == 3

    assert client.delete(f"/api/code/sessions/{a['session']['session_id']}").json() == {"ok": True}
    assert len(index) == 2
    assert index.search("alpha", project=EVERY_PROJECT) == []
    # A second click: the file is already gone, and so are the rows — not an error.
    assert client.delete(f"/api/code/sessions/{a['session']['session_id']}").json() == {"ok": False}

    deleted = client.delete("/api/code/projects", params={"workspace": ws}).json()
    assert deleted == {"deleted": 1}
    assert len(index) == 1
    assert index.search("beta", project=EVERY_PROJECT) == []
    remaining = index.search("gamma", project=EVERY_PROJECT)
    assert [h.session_id for h in remaining] == [c["session"]["session_id"]]
    assert b["session"]["session_id"] != c["session"]["session_id"]


def test_an_index_that_will_not_write_does_not_fail_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, home = _client(tmp_path, monkeypatch)

    def _refuse(self: Any, **_: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(HistoryIndex, "record", _refuse)
    response = client.post("/api/code/turn", json={"message": "still answered"})
    assert response.status_code == 200
    frames = _frames(response)
    assert frames["done"]["answer"] == "done: still answered"
    assert "error" not in frames
    assert len(history_for(home)) == 0
