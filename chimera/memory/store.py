"""Persistence for memory items (a JSON-file store)."""

from __future__ import annotations

import json
from pathlib import Path

from chimera.memory.models import MemoryItem, MemoryKind


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
            obj = MemoryItem.model_validate(item)
            self._items[obj.id] = obj

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.model_dump() for item in self._items.values()]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

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
