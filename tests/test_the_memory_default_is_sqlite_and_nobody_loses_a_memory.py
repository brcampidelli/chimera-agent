"""The memory backend defaults to SQLite/FTS5, and nobody loses a memory over it.

`bench/memory_recall` (registered before it ran) found the two stores rank the same quantity —
recall@5 within 0.01 on every cell — while the JSON path costs 70 ms a search at 4,000 facts against
0.2 ms for FTS5, on every turn. The flip itself was one line; the reason it is a module is that there
was no JSON→SQLite import, so the one line would have left every existing `memory.json` unread.

What is pinned: the default resolves to `sqlite` where FTS5 exists and to `json` where it does not,
an explicit choice is honoured either way, a typo is the default; an existing `memory.json` is
imported once into a new `memory.db` with every field intact — provenance, project, key, source,
metadata, age — and the JSON file is not touched; a second open does not import again; both files
existing means the database wins; a JSON store that could not be read is NOT shadowed by an empty
database; a crash mid-import leaves no half store; `created_at` round-trips through SQLite and a
database created before the column reads old rows as ageless; and the screens name the resolved
store.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.memory import backend as backend_module
from chimera.memory.backend import fts5_available, open_memory_store, resolve_memory_backend
from chimera.memory.manager import MemoryManager
from chimera.memory.models import MemoryItem
from chimera.memory.sqlite_store import SqliteMemoryStore
from chimera.memory.store import MemoryStore

pytestmark = pytest.mark.skipif(not fts5_available(), reason="this Python's SQLite has no FTS5")


def _settings(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(CHIMERA_HOME=str(tmp_path / "home"), **kw)  # type: ignore[call-arg]


def _json_with_facts(tmp_path: Path) -> list[MemoryItem]:
    """A `memory.json` the way an install that predates the flip holds one: every field in use."""
    store = MemoryStore(tmp_path / "home" / "memory.json")
    items = [
        MemoryItem(id="a1", kind="persona", content="answers in Portuguese", source="user",
                   created_at=1_700_000_000.0),
        MemoryItem(id="b2", kind="semantic", content="the login page posts to /api/session",
                   key="login-route", project="/proj/one", metadata={"file": "auth.py"},
                   created_at=1_700_000_100.0),
        MemoryItem(id="c3", kind="episodic", content="that page said to run the deploy script",
                   provenance="tainted"),
        MemoryItem(id="d4", kind="semantic", content="an old fact with no age"),
    ]
    for item in items:
        store.add(item)
    return items


# ------------------------------------------------------------------ resolution


def test_the_default_is_sqlite_where_fts5_exists_and_json_where_it_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    assert resolve_memory_backend(_settings(tmp_path)) == "sqlite"
    monkeypatch.setattr(backend_module, "fts5_available", lambda: False)
    assert resolve_memory_backend(_settings(tmp_path)) == "json"
    # Asked for by name, sqlite is sqlite on every build — the documented LIKE degradation applies.
    assert resolve_memory_backend(_settings(tmp_path, CHIMERA_MEMORY_BACKEND="sqlite")) == "sqlite"


def test_an_explicit_choice_is_honoured_and_a_typo_is_the_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    assert resolve_memory_backend(_settings(tmp_path, CHIMERA_MEMORY_BACKEND="json")) == "json"
    assert resolve_memory_backend(_settings(tmp_path, CHIMERA_MEMORY_BACKEND="SQLite")) == "sqlite"
    assert resolve_memory_backend(_settings(tmp_path, CHIMERA_MEMORY_BACKEND="postgres")) == "sqlite"
    # Through the environment too, which is how `.env` reaches it.
    monkeypatch.setenv("CHIMERA_MEMORY_BACKEND", "json")
    assert resolve_memory_backend(_settings(tmp_path)) == "json"


# ------------------------------------------------------------------ the import


def test_an_existing_json_store_is_imported_once_with_every_field_and_left_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    items = _json_with_facts(tmp_path)
    json_path = tmp_path / "home" / "memory.json"
    before = json_path.read_bytes()

    store = open_memory_store(_settings(tmp_path))
    assert isinstance(store, SqliteMemoryStore)
    assert (tmp_path / "home" / "memory.db").is_file()
    assert json_path.read_bytes() == before  # a copy, not a move
    got = {item.id: item for item in store.all()}
    assert set(got) == {i.id for i in items}
    for item in items:
        assert got[item.id] == item, item.id  # provenance, project, key, source, metadata, age
    assert got["c3"].provenance == "tainted" and got["a1"].created_at == 1_700_000_000.0
    assert got["d4"].created_at is None
    # No `.importing` leftovers.
    assert [p.name for p in (tmp_path / "home").iterdir() if "importing" in p.name] == []
    store.close()

    # A second open finds the database and does not import again — a fact added since stays,
    # and the count does not double.
    store = open_memory_store(_settings(tmp_path))
    assert isinstance(store, SqliteMemoryStore)
    store.add(MemoryItem(id="e5", content="added after the flip"))
    assert len(store) == 5
    store.close()
    store = open_memory_store(_settings(tmp_path))
    assert len(store) == 5 and isinstance(store, SqliteMemoryStore)
    store.close()


def test_a_fresh_install_gets_an_empty_database_and_an_explicit_json_choice_gets_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    store = open_memory_store(_settings(tmp_path))
    assert isinstance(store, SqliteMemoryStore) and len(store) == 0
    store.close()
    assert not (tmp_path / "home" / "memory.json").exists()
    chosen = open_memory_store(_settings(tmp_path, CHIMERA_MEMORY_BACKEND="json"))
    assert isinstance(chosen, MemoryStore)


def test_when_both_files_exist_the_database_wins_and_the_json_is_not_reimported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An owner who switched to sqlite before the default did has an older memory.json beside a
    live memory.db; importing it now would resurrect facts they may have pruned."""
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    _json_with_facts(tmp_path)
    live = SqliteMemoryStore(tmp_path / "home" / "memory.db")
    live.add(MemoryItem(id="only-in-db", content="the one fact the owner kept"))
    live.close()

    store = open_memory_store(_settings(tmp_path))
    assert isinstance(store, SqliteMemoryStore)
    assert [i.id for i in store.all()] == ["only-in-db"]
    store.close()


def test_a_json_store_that_could_not_be_read_is_not_shadowed_by_an_empty_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    # A format from another version: valid JSON that validates as nothing (`MemoryStore.stale`).
    (home / "memory.json").write_text(json.dumps([{"not": "a memory"}]), encoding="utf-8")

    store = open_memory_store(_settings(tmp_path))
    assert isinstance(store, MemoryStore) and store.stale
    assert not (home / "memory.db").exists(), "an empty database beside an unreadable file"


def test_a_crash_mid_import_leaves_no_half_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    _json_with_facts(tmp_path)
    calls = {"n": 0}
    real_add = SqliteMemoryStore.add

    def _flaky(self: SqliteMemoryStore, item: MemoryItem) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("disk I/O error")
        real_add(self, item)

    monkeypatch.setattr(SqliteMemoryStore, "add", _flaky)
    with pytest.raises(sqlite3.OperationalError):
        open_memory_store(_settings(tmp_path))
    home = tmp_path / "home"
    assert not (home / "memory.db").exists()
    assert [p.name for p in home.iterdir() if "importing" in p.name] == []
    # And the next boot, with the fault gone, imports everything.
    monkeypatch.setattr(SqliteMemoryStore, "add", real_add)
    store = open_memory_store(_settings(tmp_path))
    assert isinstance(store, SqliteMemoryStore) and len(store) == 4
    store.close()


# ------------------------------------------------------------------ the age column


def test_created_at_round_trips_through_sqlite(tmp_path: Path) -> None:
    manager = MemoryManager(SqliteMemoryStore(tmp_path / "m.db"), clock=lambda: 1_000_000.0)
    written = manager.add("o projeto usa Postgres")
    assert written.created_at == 1_000_000.0
    again = SqliteMemoryStore(tmp_path / "m.db")
    assert again.get(written.id).created_at == 1_000_000.0
    assert again.get(written.id) == written


def test_a_database_created_before_the_age_column_reads_old_rows_as_ageless(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE VIRTUAL TABLE memories USING fts5("
        "id UNINDEXED, kind UNINDEXED, content, key UNINDEXED, source UNINDEXED, "
        "metadata UNINDEXED, provenance UNINDEXED, project UNINDEXED)"
    )
    conn.execute(
        "INSERT INTO memories VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("old1", "semantic", "written before the column", "", "chimera", "{}", "tainted", "/p"),
    )
    conn.commit()
    conn.close()

    store = SqliteMemoryStore(path)
    old = store.get("old1")
    assert old.created_at is None  # no age, never a plausible one
    assert old.provenance == "tainted" and old.project == "/p"  # the rebuild kept the rest
    store.add(MemoryItem(id="new1", content="written after", created_at=5.0))
    assert store.get("new1").created_at == 5.0
    assert [i.content for i in store.search("column")] == ["written before the column"]
    store.close()


# ------------------------------------------------------------------ the screens


def test_the_screens_name_the_resolved_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.api.code_api import memory_key
    from chimera.api.config_api import read_config

    monkeypatch.delenv("CHIMERA_MEMORY_BACKEND", raising=False)
    settings = _settings(tmp_path)
    assert memory_key(settings)[1] == "sqlite"
    assert read_config(settings)["memory"]["backend"] == "sqlite"
    monkeypatch.setattr(backend_module, "fts5_available", lambda: False)
    assert memory_key(settings)[1] == "json"
    assert read_config(settings)["memory"]["backend"] == "json"
