"""Tests for the optional SQLite + FTS5 memory store."""

from __future__ import annotations

import uuid
from pathlib import Path

from chimera.memory import MemoryItem, MemoryManager, SqliteMemoryStore


def _item(content: str, *, kind: str = "semantic", key: str | None = None) -> MemoryItem:
    return MemoryItem(id=uuid.uuid4().hex[:8], kind=kind, content=content, key=key, source="test")  # type: ignore[arg-type]


def test_provenance_round_trips_through_sqlite(tmp_path: Path) -> None:
    # SECURITY: a tainted memory must not launder itself to "clean" via the SQLite backend.
    store = SqliteMemoryStore(tmp_path / "m.db")
    item = MemoryItem(id=uuid.uuid4().hex, kind="semantic", content="poisoned fact",  # type: ignore[arg-type]
                      source="test", provenance="tainted")
    store.add(item)
    assert store.get(item.id).provenance == "tainted"
    assert store.all()[0].provenance == "tainted"
    assert store.by_kind("semantic")[0].provenance == "tainted"


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


def test_the_store_answers_from_a_thread_it_was_not_made_on(tmp_path: Path) -> None:
    """Live on 0.59.0-dev, with SQLite the default: a guest's message on a shared conversation ran
    the turn on the guest listener's thread, recall reached the manager the app had built at
    boot, and SQLite refused — `ProgrammingError: SQLite objects created in a thread can only be
    used in that same thread`. The Discord bot recalls from its own thread too, and the desktop's
    messaging adapters; every one of them was one message away from the same error. The
    connection is shared under a lock now, and this test is the reproduction: without
    `check_same_thread=False` it raises on the first statement."""
    import threading

    store = SqliteMemoryStore(tmp_path / "m.db")
    store.add(MemoryItem(id="a", content="the login page posts to /api/session"))
    seen: list[object] = []

    def worker() -> None:
        try:
            store.add(MemoryItem(id="b", content="written from another thread"))
            seen.append([i.content for i in store.search("login")])
            seen.append(len(store))
        except Exception as exc:  # noqa: BLE001 — the failure IS the finding
            seen.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(5)
    assert seen == [["the login page posts to /api/session"], 2], seen
    # And many threads at once, each reading and writing, leave a consistent store behind.
    def hammer(n: int) -> None:
        for i in range(20):
            store.add(MemoryItem(id=f"t{n}-{i}", content=f"fact {n} {i} thread"))
            store.search("thread")
            store.all()

    threads = [threading.Thread(target=hammer, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert len(store) == 2 + 4 * 20
