"""Persistence for memory items (a JSON-file store).

The write was atomic long before the *update* was. ``save()`` has always written through a temp file
and replaced, so a crash mid-write cannot truncate the store — but ``add()`` is a read-modify-write
over a snapshot taken in ``__init__``, and two writers each doing that lose one of the two records
with nothing raised and nothing logged.

That is the ordinary configuration, not a corner: ``chimera serve`` runs the cron daemon in a thread
of the gateway process, so any ``chimera memory add`` typed over SSH while the daemon is up is a
second process holding a stale snapshot of the same file.

So every mutation now happens under an advisory lock and **re-reads the file first**, applying its
own change on top of whatever else landed. Reads stay lock-free: the atomic replace means a reader
sees a complete old version or a complete new one, never a torn one.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from chimera.core.filelock import atomic_write_text, locked, read_text
from chimera.memory.models import MemoryItem, MemoryKind
from chimera.telemetry import get_logger

_log = get_logger("memory.store")


class MemoryBackend(Protocol):
    """What the MemoryManager needs of a store — satisfied by JSON and SQLite backends."""

    def add(self, item: MemoryItem) -> None: ...
    def get(self, item_id: str) -> MemoryItem: ...
    def remove(self, item_id: str) -> None: ...
    def all(self) -> list[MemoryItem]: ...
    def by_kind(self, kind: MemoryKind) -> list[MemoryItem]: ...
    def __len__(self) -> int: ...


class MemoryStore:
    """A JSON-file-backed collection of :class:`MemoryItem` objects."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._items: dict[str, MemoryItem] = {}
        # Serialises threads sharing this instance. Two *instances* in one process (and two
        # processes) are serialised by the file lock instead — always taken second, so the ordering
        # is consistent and there is no cycle to deadlock on.
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        self._items = {}
        if not self.path.exists():
            return
        raw = json.loads(read_text(self.path) or "[]")
        for item in raw:
            # Skip a single malformed record (hand-edit, schema drift, truncated last object) instead
            # of aborting the whole load — one bad entry must not lose every other memory.
            try:
                obj = MemoryItem.model_validate(item)
            except ValueError as exc:
                _log.warning("skipping malformed memory record: %s", exc)
                continue
            self._items[obj.id] = obj

    def _write(self) -> None:
        """Serialise the current items to disk atomically. Assumes the caller holds the locks."""
        data = [item.model_dump() for item in self._items.values()]
        atomic_write_text(self.path, json.dumps(data, indent=2))

    def save(self) -> None:
        """Write the in-memory items out, replacing whatever is on disk.

        This is the blunt form and it is kept for callers that hold the whole picture. It does
        **not** merge concurrent writes — use :meth:`add` / :meth:`remove`, which do.
        """
        with self._lock, locked(self.path):
            self._write()

    def _mutate(self, apply: Callable[[dict[str, MemoryItem]], None]) -> None:
        """Re-read, apply one change on top, write back — all under the lock.

        The reload is the part that matters. Without it every mutation writes a dict built from
        whatever the file held when this object was constructed, so a second writer's records
        vanish on the next save with nothing to show it happened.
        """
        with self._lock, locked(self.path):
            self.load()
            apply(self._items)
            self._write()

    def add(self, item: MemoryItem) -> None:
        def put(items: dict[str, MemoryItem]) -> None:
            items[item.id] = item

        self._mutate(put)

    def get(self, item_id: str) -> MemoryItem:
        return self._items[item_id]

    def remove(self, item_id: str) -> None:
        def drop(items: dict[str, MemoryItem]) -> None:
            items.pop(item_id, None)

        self._mutate(drop)

    def all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def by_kind(self, kind: MemoryKind) -> list[MemoryItem]:
        return [item for item in self._items.values() if item.kind == kind]

    def __len__(self) -> int:
        return len(self._items)
