"""Persistence for memory items (a JSON-file store)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

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
        self.load()

    def load(self) -> None:
        self._items = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            # Skip a single malformed record (hand-edit, schema drift, truncated last object) instead
            # of aborting the whole load — one bad entry must not lose every other memory.
            try:
                obj = MemoryItem.model_validate(item)
            except ValueError as exc:
                _log.warning("skipping malformed memory record: %s", exc)
                continue
            self._items[obj.id] = obj

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.model_dump() for item in self._items.values()]
        # Atomic write (temp + replace): a crash or ENOSPC mid-write must not truncate the store and
        # make every memory unreadable on next load. os.replace is atomic on the same filesystem.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, item: MemoryItem) -> None:
        self._items[item.id] = item
        self.save()

    def get(self, item_id: str) -> MemoryItem:
        return self._items[item_id]

    def remove(self, item_id: str) -> None:
        self._items.pop(item_id, None)
        self.save()

    def all(self) -> list[MemoryItem]:
        return list(self._items.values())

    def by_kind(self, kind: MemoryKind) -> list[MemoryItem]:
        return [item for item in self._items.values() if item.kind == kind]

    def __len__(self) -> int:
        return len(self._items)
