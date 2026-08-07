"""Past coding conversations, listed and filed under the project they belong to.

A list of old conversations with no project on each row is a pile: you can see that you asked
something last Tuesday, not which codebase you asked it about — which is most of what makes an old
conversation findable at all. The field exists so a sidebar can group by project the way a coding
tool does, and these pin the parts of that which are easy to get subtly wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.code_session import CodeSession, CodeSessionStore


class _Agent:
    def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - never called here
        raise AssertionError("listing must not need an agent")


def _session(tmp_path: Path, **kw: Any) -> CodeSessionStore:
    return CodeSessionStore(tmp_path / "code_sessions")


def test_a_session_remembers_which_project_it_was_about(tmp_path: Path) -> None:
    store = _session(tmp_path)
    store.save(CodeSession(_Agent(), session_id="a", workspace="/home/me/api"))

    assert store.load("a", _Agent()).workspace == "/home/me/api"


def test_the_list_carries_the_project_and_the_first_question(tmp_path: Path) -> None:
    store = _session(tmp_path)
    store.save(
        CodeSession(
            _Agent(),
            session_id="a",
            workspace="/home/me/api",
            messages=[
                {"role": "user", "content": "fix the login redirect"},
                {"role": "assistant", "content": "done"},
                {"role": "user", "content": "now the logout one"},
            ],
        )
    )

    (row,) = store.list_meta()
    assert row["id"] == "a"
    assert row["workspace"] == "/home/me/api"
    # The FIRST question, not the last: a conversation is findable by how it started, not by
    # whatever it drifted to three turns later.
    assert row["title"] == "fix the login redirect"
    assert row["turns"] == 2  # user messages, not transcript entries


def test_a_title_is_one_line_however_the_question_was_pasted(tmp_path: Path) -> None:
    """A pasted stack trace must not become a five-line row in a sidebar."""
    store = _session(tmp_path)
    store.save(
        CodeSession(
            _Agent(),
            session_id="a",
            messages=[{"role": "user", "content": "  fix this:\n\n  Traceback\n    line 2  "}],
        )
    )

    assert store.list_meta()[0]["title"] == "fix this: Traceback line 2"


def test_an_unreadable_file_is_omitted_rather_than_breaking_the_whole_list(tmp_path: Path) -> None:
    """One interrupted write must not make the sidebar say "you have no conversations"."""
    store = _session(tmp_path)
    store.save(CodeSession(_Agent(), session_id="good", messages=[{"role": "user", "content": "hi"}]))
    (store.root / "broken.json").write_text("{not json", encoding="utf-8")

    rows = store.list_meta()
    assert [r["id"] for r in rows] == ["good"]


def test_newest_first(tmp_path: Path) -> None:
    import os
    import time

    store = _session(tmp_path)
    store.save(CodeSession(_Agent(), session_id="older", messages=[{"role": "user", "content": "a"}]))
    store.save(CodeSession(_Agent(), session_id="newer", messages=[{"role": "user", "content": "b"}]))
    # Explicit mtimes: two writes in the same millisecond would make the order a coin flip, and a
    # test that passes on a coin flip is worse than no test.
    now = time.time()
    os.utime(store.root / "older.json", (now - 100, now - 100))
    os.utime(store.root / "newer.json", (now, now))

    assert [r["id"] for r in store.list_meta()] == ["newer", "older"]


def test_an_empty_store_lists_nothing_rather_than_failing(tmp_path: Path) -> None:
    assert _session(tmp_path).list_meta() == []
