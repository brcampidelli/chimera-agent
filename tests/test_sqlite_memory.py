"""Tests for the optional SQLite + FTS5 memory store."""

from __future__ import annotations

import uuid
from pathlib import Path

from chimera.memory import MemoryItem, MemoryManager, SqliteMemoryStore


def _item(content: str, *, kind: str = "semantic", key: str | None = None) -> MemoryItem:
    return MemoryItem(id=uuid.uuid4().hex[:8], kind=kind, content=content, key=key, source="test")  # type: ignore[arg-type]


def test_add_get_all_remove_roundtrip(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "m.db")
    item = _item("Alex prefers absolute imports")
    store.add(item)
    assert len(store) == 1
    assert store.get(item.id).content == "Alex prefers absolute imports"
    assert [x.content for x in store.all()] == ["Alex prefers absolute imports"]
    store.remove(item.id)
    assert len(store) == 0


def test_add_replaces_same_id(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "m.db")
    item = _item("v1")
    store.add(item)
    item.content = "v2"
    store.add(item)
    assert len(store) == 1 and store.get(item.id).content == "v2"


def test_by_kind_and_metadata(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "m.db")
    persona = _item("prefers concise answers", kind="persona")
    persona.metadata = {"weight": "high"}
    store.add(persona)
    store.add(_item("some semantic fact", kind="semantic"))
    kept = store.by_kind("persona")
    assert [x.content for x in kept] == ["prefers concise answers"]
    assert kept[0].metadata == {"weight": "high"}  # metadata survives the round-trip


def test_search_finds_by_content(tmp_path: Path) -> None:
    store = SqliteMemoryStore(tmp_path / "m.db")
    store.add(_item("the deploy pipeline uses GitHub Actions"))
    store.add(_item("lunch is at noon"))
    hits = store.search("deploy pipeline", k=5)
    assert len(hits) == 1 and "deploy" in hits[0].content
    assert store.search("", k=5) == []


def test_manager_delegates_to_backend_search(tmp_path: Path) -> None:
    manager = MemoryManager(SqliteMemoryStore(tmp_path / "m.db"))
    manager.remember("Stripe is our payment provider")
    manager.remember("we deploy on Fridays")
    hits = manager.search("payment", k=5)
    assert any("Stripe" in hit.content for hit in hits)
