"""A conversation whose file does not parse is read from its previous copy, or listed as damaged.

Observed 2026-10-18 (study 34, item 9): four conversations from 16/10 whose JSON did not parse
("Expecting property name …") were left out of the list with one log line each — to the owner they
had vanished — and the next turn sent to one of them would have written a fresh file over it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.core.code_session import CodeSession, CodeSessionStore


class _NoAgent:
    def run(self, *args: Any, **kwargs: Any) -> Any:  # never called: nothing here sends a turn
        raise AssertionError("no turn is run in these tests")


def _saved(store: CodeSessionStore, words: str, session_id: str = "abc123") -> CodeSession:
    session = CodeSession(_NoAgent(), session_id=session_id, workspace="/w")
    session.messages = [
        {"role": "user", "content": words},
        {"role": "assistant", "content": "ok"},
    ]
    store.save(session)
    return session


def _truncate(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(text[: len(text) // 2], encoding="utf-8")


def test_a_save_keeps_the_previous_good_copy(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code")
    _saved(store, "first version")
    _saved(store, "second version")
    backup = tmp_path / "code" / "abc123.json.bak"
    assert json.loads(backup.read_text(encoding="utf-8"))["messages"][0]["content"] == (
        "first version"
    )
    # The backup is never listed as a conversation of its own.
    assert [row["id"] for row in store.list_meta()] == ["abc123"]


def test_a_truncated_file_is_read_from_its_backup(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code")
    _saved(store, "first version")
    _saved(store, "second version")
    _truncate(tmp_path / "code" / "abc123.json")

    [row] = CodeSessionStore(tmp_path / "code").list_meta()
    assert row["id"] == "abc123" and not row.get("damaged")
    assert row["title"] == "first version"
    loaded = CodeSessionStore(tmp_path / "code").load("abc123", _NoAgent())
    assert loaded.messages[0]["content"] == "first version"


def test_with_no_readable_copy_it_is_listed_as_damaged_and_kept(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path / "code")
    _saved(store, "only version")
    path = tmp_path / "code" / "abc123.json"
    _truncate(path)
    broken = path.read_text(encoding="utf-8")

    [row] = CodeSessionStore(tmp_path / "code").list_meta()
    assert row["damaged"] is True and row["id"] == "abc123"
    assert row["title"].startswith("Damaged conversation (") and "abc123" in row["title"]
    assert "JSONDecodeError" in row["damage"]
    # Listing it changed nothing on disk.
    assert path.read_text(encoding="utf-8") == broken

    # A turn in it starts fresh in memory, and its save sets the damaged text aside first.
    again = CodeSessionStore(tmp_path / "code")
    session = again.load("abc123", _NoAgent())
    assert session.messages == []
    session.messages = [{"role": "user", "content": "new start"}]
    again.save(session)
    assert (tmp_path / "code" / "abc123.json.damaged").read_text(encoding="utf-8") == broken
    # ...and the damaged text never became the backup.
    assert not (tmp_path / "code" / "abc123.json.bak").exists()


def test_the_api_row_carries_the_damage(tmp_path: Path) -> None:
    from chimera.api.schemas import CodeSessionMetaOut

    store = CodeSessionStore(tmp_path / "code")
    _saved(store, "only version")
    _truncate(tmp_path / "code" / "abc123.json")
    [row] = CodeSessionStore(tmp_path / "code").list_meta()
    out = CodeSessionMetaOut.model_validate(row)
    assert out.damaged is True and out.damage
