"""Optional SQLite + FTS5 memory store — full-text recall that scales past the JSON store.

The default :class:`~chimera.memory.store.MemoryStore` keeps everything in one JSON file and
recalls by keyword-token overlap. This backend keeps memories in SQLite with an FTS5 index,
so recall is phrase/substring-aware and stays fast as the corpus grows. It exposes a
``search`` method that :class:`~chimera.memory.manager.MemoryManager` prefers when present.
FTS5 ships with most Python builds; if it is missing, this degrades to a ``LIKE`` search.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from chimera.memory.models import EVERY_PROJECT, MemoryItem, MemoryKind

# NOTE: provenance is a first-class column, not folded into metadata: it is a SECURITY signal
# (a tainted memory must never launder itself to "clean"), and it must round-trip identically to the
# JSON store or the guarantee breaks purely by backend choice.
_COLUMNS = "id, kind, content, key, source, metadata, provenance, project"


class SqliteMemoryStore:
    """A SQLite-backed memory store with FTS5 (or LIKE) search."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._fts = self._init_schema()
        # Order matters: provenance first, because it rebuilds from a six-column layout and
        # the project migration reads the seven-column one.
        self._migrate_provenance()
        self._migrate_project()

    def _init_schema(self) -> bool:
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories USING fts5("
                "id UNINDEXED, kind UNINDEXED, content, key UNINDEXED, source UNINDEXED, "
                "metadata UNINDEXED, provenance UNINDEXED, project UNINDEXED)"
            )
            self._conn.commit()
            return True
        except sqlite3.OperationalError:  # FTS5 not compiled in — fall back to a plain table
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS memories ("
                "id TEXT PRIMARY KEY, kind TEXT, content TEXT, key TEXT, source TEXT, "
                "metadata TEXT, provenance TEXT DEFAULT 'clean', project TEXT)"
            )
            self._conn.commit()
            return False

    def _migrate_project(self) -> None:
        """Add the project column to a store created before it existed (rows stay global).

        Same shape as the provenance migration below and for the same reason: an FTS5
        virtual table cannot ALTER, so it is rebuilt. A row written before scoping existed
        has no project and therefore belongs everywhere, which is the behaviour it always
        had — an upgrade must not hide somebody's memories behind a folder they never chose.
        """
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "project" in cols:
            return
        old = "id, kind, content, key, source, metadata, provenance"
        rows = self._conn.execute(f"SELECT {old} FROM memories").fetchall()
        if self._fts:
            self._conn.execute("DROP TABLE memories")
            self._init_schema()
            if rows:
                self._conn.executemany(
                    f"INSERT INTO memories ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                    rows,
                )
        else:
            self._conn.execute("ALTER TABLE memories ADD COLUMN project TEXT")
        self._conn.commit()

    def _migrate_provenance(self) -> None:
        """Add the provenance column to a store created before it existed (rows default to 'clean').

        A plain table can ALTER; an FTS5 virtual table cannot, so rebuild it (memory stores are small).
        Old rows predate provenance tracking, so 'clean' is the honest default for them.
        """
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(memories)").fetchall()}
        if "provenance" in cols:
            return
        old = "id, kind, content, key, source, metadata"
        rows = self._conn.execute(f"SELECT {old} FROM memories").fetchall()
        if self._fts:
            self._conn.execute("DROP TABLE memories")
            self._init_schema()
        else:
            self._conn.execute("ALTER TABLE memories ADD COLUMN provenance TEXT DEFAULT 'clean'")
        if rows and self._fts:
            self._conn.executemany(
                f"INSERT INTO memories ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, 'clean')", rows
            )
        self._conn.commit()

    @staticmethod
    def _to_item(row: tuple[str, str, str, str, str, str, str, str | None]) -> MemoryItem:
        item_id, kind, content, key, source, metadata, provenance, project = row
        return MemoryItem(
            id=item_id,
            kind=kind,  # type: ignore[arg-type]
            content=content,
            key=key or None,
            source=source,
            metadata=json.loads(metadata) if metadata else {},
            provenance=provenance or "clean",
            # Empty string and NULL both mean global. SQLite hands back one or the other
            # depending on whether the row predates the column, and a fact scoped to a
            # project named "" is not a thing.
            project=project or None,
        )

    def add(self, item: MemoryItem) -> None:
        self._conn.execute("DELETE FROM memories WHERE id = ?", (item.id,))
        self._conn.execute(
            f"INSERT INTO memories ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (item.id, item.kind, item.content, item.key or "", item.source,
             json.dumps(item.metadata), item.provenance, item.project),
        )
        self._conn.commit()

    def get(self, item_id: str) -> MemoryItem:
        row = self._conn.execute(f"SELECT {_COLUMNS} FROM memories WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            raise KeyError(item_id)
        return self._to_item(row)

    def remove(self, item_id: str) -> None:
        self._conn.execute("DELETE FROM memories WHERE id = ?", (item_id,))
        self._conn.commit()

    def all(self) -> list[MemoryItem]:
        # ORDER BY rowid = stable insertion order (oldest first), which value.rank's positional
        # recency proxy relies on. Without it the order is unspecified and prune keeps the wrong items.
        rows = self._conn.execute(f"SELECT {_COLUMNS} FROM memories ORDER BY rowid").fetchall()
        return [self._to_item(row) for row in rows]

    def by_kind(self, kind: MemoryKind) -> list[MemoryItem]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM memories WHERE kind = ? ORDER BY rowid", (kind,)
        ).fetchall()
        return [self._to_item(row) for row in rows]

    def search(
        self, query: str, k: int = 5, *, project: str | None = EVERY_PROJECT
    ) -> list[MemoryItem]:
        """Full-text recall (FTS5 MATCH; LIKE fallback), best matches first.

        Through the same tokenizer and the same function-word list the keyword path uses. This
        backend has its own search and so bypassed both: with the JSON store fixed, *"o que e isso?"*
        recalled nothing, and against SQLite it still recalled every fact in the store. One rule
        about what a query means, or the answer depends on which backend the owner picked.
        """
        from chimera.memory.tokens import informative, tokens

        terms = sorted(informative(tokens(query)))
        if not terms:
            return []
        # Scoped in SQL, not afterwards: a LIMIT applied before the filter returns the wrong
        # page — k rows of other projects while this project's sat below the cut.
        escopo_params: list[object]
        if project == EVERY_PROJECT:
            escopo, escopo_params = "", []
        elif project is None:
            escopo, escopo_params = " AND project IS NULL", []
        else:
            escopo, escopo_params = " AND (project IS NULL OR project = ?)", [project]

        if self._fts:
            match = " OR ".join(terms)
            try:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM memories WHERE content MATCH ?{escopo} "
                    "ORDER BY rank LIMIT ?",
                    (match, *escopo_params, k),
                ).fetchall()
                if rows:
                    return [self._to_item(row) for row in rows]
                # Nothing, rather than an error — fall through to LIKE instead of returning empty.
                # FTS5's tokenizer indexes a run of Han as ONE token, while `tokens` splits it per
                # character so a query can reach inside a sentence; those two disagree, and LIKE
                # (a substring match) is the one that can still find it.
            except sqlite3.OperationalError:
                pass  # malformed FTS query — fall through to LIKE
        clause = " OR ".join("lower(content) LIKE ?" for _ in terms)
        params: list[object] = [f"%{term}%" for term in terms]
        params.extend(escopo_params)
        params.append(k)
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM memories WHERE ({clause}){escopo} LIMIT ?", params
        ).fetchall()
        return [self._to_item(row) for row in rows]

    def __len__(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
