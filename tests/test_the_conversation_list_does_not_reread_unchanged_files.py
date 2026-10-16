"""The conversation list reads a stored conversation again only when its file changed.

Measured on 2026-10-16 in the installed app: with ~15 Code turns running at once, the sidebar asked
for the list several times a second (it refetches whenever the set of running turns changes), and
every call opened and parsed all 69 stored conversations — some over 190 KB, four of them corrupt
and logging a warning each time. That is CPU under the GIL in the server process, proportional to
conversations times calls, and it stalled the backend until the supervisor killed it and every turn
with it. These pin the cache that replaced the full re-read, and that its answer did not change.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from chimera.core import code_session as code_session_module
from chimera.core.code_session import (
    CodeSession,
    CodeSessionStore,
    _is_person_turn,
    _title_from,
)


class _Agent:
    def run(self, task: str, **_: Any) -> Any:  # pragma: no cover - never called here
        raise AssertionError("listing must not need an agent")


class _Reads:
    """Counts `Path.read_text` calls per file name, the one way the store reads a conversation."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.counts: Counter[str] = Counter()
        original = Path.read_text

        def spy(path: Path, *args: Any, **kwargs: Any) -> str:
            self.counts[path.name] += 1
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", spy)

    def reset(self) -> None:
        self.counts.clear()


class _Warnings:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        real = code_session_module._log

        class _Log:
            def warning(_self, msg: str, *args: Any) -> None:
                self.calls.append(msg % args)

            def __getattr__(_self, name: str) -> Any:
                return getattr(real, name)

        monkeypatch.setattr(code_session_module, "_log", _Log())


def _store(tmp_path: Path) -> CodeSessionStore:
    return CodeSessionStore(tmp_path / "code_sessions")


def _seed(store: CodeSessionStore, n: int = 3) -> None:
    for i in range(n):
        store.save(
            CodeSession(
                _Agent(),
                session_id=f"s{i}",
                workspace=f"/w/{i}",
                messages=[
                    {"role": "user", "content": f"question {i}"},
                    {"role": "assistant", "content": "answer"},
                ],
            )
        )


def _bump(path: Path, text: str) -> None:
    """Rewrite a file and move its mtime forward, so the change is visible on any clock."""
    st = path.stat()
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))


def test_two_lists_in_a_row_read_each_file_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _seed(store)
    # A fresh store, as after a restart: nothing cached from the saves.
    fresh = _store(tmp_path)
    reads = _Reads(monkeypatch)

    first = fresh.list_meta()
    second = fresh.list_meta()

    assert first == second
    assert len(first) == 3
    assert reads.counts == Counter({"s0.json": 1, "s1.json": 1, "s2.json": 1})


def test_a_changed_file_is_the_only_one_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _seed(store)
    fresh = _store(tmp_path)
    reads = _Reads(monkeypatch)
    fresh.list_meta()
    reads.reset()

    path = tmp_path / "code_sessions" / "s1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["workspace"] = "/w/moved"
    _bump(path, json.dumps(data))
    reads.reset()

    rows = {r["id"]: r for r in fresh.list_meta()}

    assert reads.counts == Counter({"s1.json": 1})
    assert rows["s1"]["workspace"] == "/w/moved"


def test_a_deleted_file_leaves_the_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = _store(tmp_path)
    _seed(store)
    store.list_meta()

    (tmp_path / "code_sessions" / "s0.json").unlink()  # behind the store's back
    assert sorted(r["id"] for r in store.list_meta()) == ["s1", "s2"]

    assert store.delete("s1")  # through the store
    assert [r["id"] for r in store.list_meta()] == ["s2"]


def test_a_save_shows_in_the_next_list_without_reading_any_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _seed(store)
    store.list_meta()
    reads = _Reads(monkeypatch)

    store.save(
        CodeSession(
            _Agent(),
            session_id="s1",
            workspace="/w/1",
            messages=[
                {"role": "user", "content": "question 1"},
                {"role": "assistant", "content": "answer"},
                {"role": "user", "content": "and a second turn"},
            ],
        )
    )
    store.save(
        CodeSession(
            _Agent(),
            session_id="new",
            messages=[{"role": "user", "content": "brand new"}],
        )
    )
    rows = {r["id"]: r for r in store.list_meta()}

    assert sum(reads.counts.values()) == 0
    assert rows["s1"]["turns"] == 2
    assert rows["new"]["title"] == "brand new"


def test_a_corrupt_file_warns_once_and_stays_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    _seed(store, n=1)
    bad = tmp_path / "code_sessions" / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    warnings = _Warnings(monkeypatch)
    reads = _Reads(monkeypatch)

    for _ in range(10):
        assert [r["id"] for r in store.list_meta()] == ["s0"]

    assert len([w for w in warnings.calls if "broken" in w]) == 1
    assert reads.counts["broken.json"] == 1

    # A new version of the still-broken file is a new thing to say once about.
    _bump(bad, "{still not json at all")
    for _ in range(3):
        store.list_meta()
    assert len([w for w in warnings.calls if "broken" in w]) == 2


def test_rows_handed_out_do_not_write_back_into_the_cache(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _seed(store, n=1)
    (row,) = store.list_meta()
    row["running"] = True
    row["title"] = "decorated by a caller"

    (again,) = store.list_meta()
    assert "running" not in again
    assert again["title"] == "question 0"


def _old_list_meta(root: Path) -> list[dict[str, Any]]:
    """The implementation before the cache, reproduced to compare answers."""
    out: list[dict[str, Any]] = []
    for path in root.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        except (OSError, ValueError):
            continue
        receipts = data.get("receipts")
        last = receipts[-1] if isinstance(receipts, list) and receipts else None
        verified = last.get("verified") if isinstance(last, dict) else None
        last_verdict = str(verified.get("state") or "") if isinstance(verified, dict) else ""
        out.append(
            {
                "id": str(data.get("session_id") or path.stem),
                "title": _title_from(messages),
                "workspace": str(data.get("workspace") or ""),
                "turns": sum(1 for m in messages if _is_person_turn(m)),
                "updated_at": path.stat().st_mtime,
                "last_verdict": last_verdict,
            }
        )
    out.sort(key=lambda m: float(m["updated_at"]), reverse=True)
    return out


def test_the_answer_is_the_one_the_old_list_gave(tmp_path: Path) -> None:
    store = _store(tmp_path)
    root = tmp_path / "code_sessions"
    _seed(store)
    store.save(
        CodeSession(
            _Agent(),
            session_id="verdict",
            workspace="/w/v",
            messages=[{"role": "user", "content": "with a receipt"}],
            receipts=[{"verified": {"state": "passed"}}],
        )
    )
    # Written by hand: no session_id (the stem stands in), no workspace, a stray non-dict message.
    (root / "bare.json").write_text(
        json.dumps({"messages": ["junk", {"role": "user", "content": "hand written"}]}),
        encoding="utf-8",
    )
    (root / "corrupt.json").write_text("{", encoding="utf-8")
    for i, path in enumerate(sorted(root.glob("*.json"))):
        os.utime(path, ns=(1_700_000_000_000_000_000 + i, 1_700_000_000_000_000_000 + i * 10**9))

    expected = _old_list_meta(root)
    assert store.list_meta() == expected
    assert _store(tmp_path).list_meta() == expected  # cold
    assert store.list_meta() == expected  # warm
    assert {r["id"] for r in expected} == {"s0", "s1", "s2", "verdict", "bare"}
