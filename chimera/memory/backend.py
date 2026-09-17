"""Which memory store this home reads — and the one-time import that keeps a flip from losing facts.

``CHIMERA_MEMORY_BACKEND`` defaults to ``sqlite`` since 0.59.0. The measurement that decided it is
``bench/memory_recall`` (registered before it ran): on 4,034 one-sentence facts and 1,200 queries in
four styles, recall@5 of the two stores differs by at most 0.01 on every cell — they rank the same
quantity, an IDF sum, and disagree on ties — while the JSON path re-tokenizes the whole store on
every query and its median search grows from 3 ms at 200 facts to 70 ms at 4,000, against 0.2 ms
for FTS5 at every size. Recall runs on every turn, so the 70 ms is paid on every turn.

The flip had a cost the bench could not see, and it is the reason this module exists rather than a
one-line default change: there was no JSON→SQLite import in the product. Flipping the default would
have left every existing ``memory.json`` unread — a memory that silently vanishes, the family of
defect this project keeps a file about. So :func:`open_memory_store` does three things, in order:

1. **Resolves** the backend. An explicit setting is honoured as written. The default is ``sqlite``
   when the Python build has FTS5 and ``json`` when it does not: the SQLite store degrades to a
   ``LIKE`` search without FTS5, which has no ranking at all, and a default must not trade the
   ranked recall the JSON path has for an unranked one. Explicitly asking for ``sqlite`` on such a
   build gets the documented degradation, because that is what was asked.
2. **Imports** once. Opening a ``memory.db`` that does not exist yet, beside a ``memory.json`` that
   does, copies every fact into the new store — id, kind, key, source, metadata, provenance,
   project and age all preserved, so a tainted fact stays tainted and an old fact stays ageless.
   Written to a sibling file and renamed into place, so a crash mid-import leaves no half store
   that the next boot would mistake for a finished one. The JSON file is **not touched**: it is
   the person's data, and the import is a copy, not a move.
3. **Refuses** to shadow what it could not read. A ``memory.json`` that exists but did not load
   (``MemoryStore.stale``: a format from another version) keeps being the store in use, with the
   error the JSON store already logs — creating an empty database beside it would turn "these
   facts did not load" into "these facts are gone".

Both files existing means the owner switched before this default did; the database wins and the
JSON file is left alone, as it always was for them.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from chimera.memory.store import MemoryBackend, MemoryStore
from chimera.telemetry import get_logger

if TYPE_CHECKING:
    from chimera.config import Settings

_log = get_logger("memory.backend")

JSON_FILE = "memory.json"
SQLITE_FILE = "memory.db"
BACKENDS = ("json", "sqlite")


def fts5_available() -> bool:
    """Whether this Python's SQLite was built with FTS5 — the thing the default depends on."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return False
    return True


def resolve_memory_backend(settings: Settings) -> str:
    """``"json"`` or ``"sqlite"``: the store this home's settings mean.

    A value the owner wrote is returned as written (lower-cased). The default resolves to
    ``sqlite`` only when FTS5 exists; otherwise ``json``, for the ranking reason in the module
    docstring. An unknown value is reported, and read as the default — a typo in ``.env`` must
    not pick a store by accident.
    """
    raw = (settings.memory_backend or "").strip().lower()
    explicit = "memory_backend" in settings.model_fields_set
    if raw not in BACKENDS:
        if raw:
            _log.warning("CHIMERA_MEMORY_BACKEND=%r is not one of %s; using the default", raw, BACKENDS)
        raw, explicit = "sqlite", False
    if raw == "sqlite" and not explicit and not fts5_available():
        return "json"
    return raw


def open_memory_store(settings: Settings) -> MemoryBackend:
    """The store for this home: resolved, imported once if needed, never shadowing what it could
    not read. See the module docstring for the three rules."""
    from chimera.memory.sqlite_store import SqliteMemoryStore

    home = Path(settings.home)
    json_path = home / JSON_FILE
    db_path = home / SQLITE_FILE
    backend = resolve_memory_backend(settings)
    if backend == "json":
        return MemoryStore(json_path)
    if not db_path.exists() and json_path.exists():
        source = MemoryStore(json_path)
        if source.stale:
            _log.error(
                "memory: %s exists but could not be read, so no %s is created beside it — the "
                "JSON store stays in use until the file is readable again",
                json_path, SQLITE_FILE,
            )
            return source
        imported = _import(source, db_path)
        _log.info("memory: imported %d fact(s) from %s into %s", imported, json_path.name, db_path.name)
    return SqliteMemoryStore(db_path)


def _import(source: MemoryStore, db_path: Path) -> int:
    """Copy every item of ``source`` into a NEW database at ``db_path``, atomically. Returns the count.

    Written to a sibling temp file and renamed into place: a crash halfway leaves the temp file,
    not a half-filled ``memory.db`` that the next boot would take for the finished store and stop
    importing into.
    """
    from chimera.memory.sqlite_store import SqliteMemoryStore

    items = source.all()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(f"{db_path.name}.{os.getpid()}.{uuid.uuid4().hex}.importing")
    store = SqliteMemoryStore(tmp)
    try:
        for item in items:
            store.add(item)
        store.close()
        os.replace(tmp, db_path)
    except BaseException:
        store.close()
        tmp.unlink(missing_ok=True)
        raise
    return len(items)
