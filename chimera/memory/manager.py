"""The Memory Manager — curated memory with ADD/UPDATE/DELETE/NOOP operations.

Inspired by Memory-R1: instead of appending blindly, ``remember`` decides whether a
new fact is genuinely new (ADD), updates an existing one (UPDATE), or is a duplicate
(NOOP). ``merge`` applies this over a batch — the dedup engine behind the migration
memory-merge (which must never overwrite existing history blindly).
"""

from __future__ import annotations

import re
import uuid

from chimera.memory.models import MemoryItem, MemoryKind
from chimera.memory.store import MemoryBackend
from chimera.telemetry import get_logger

_log = get_logger("memory.manager")
_TOKEN = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


class MemoryManager:
    """Curates a :class:`MemoryStore`."""

    def __init__(self, store: MemoryBackend) -> None:
        self.store = store

    def add(
        self,
        content: str,
        kind: MemoryKind = "semantic",
        *,
        key: str | None = None,
        source: str = "chimera",
    ) -> MemoryItem:
        item = MemoryItem(
            id=uuid.uuid4().hex[:8], kind=kind, content=content, key=key, source=source
        )
        self.store.add(item)
        return item

    def update(self, item_id: str, content: str) -> MemoryItem:
        item = self.store.get(item_id)
        item.content = content
        self.store.add(item)
        return item

    def delete(self, item_id: str) -> None:
        self.store.remove(item_id)

    def _find_duplicate(self, content: str, key: str | None) -> MemoryItem | None:
        norm = _normalize(content)
        for item in self.store.all():
            if key is not None and item.key == key:
                return item
            if _normalize(item.content) == norm:
                return item
        return None

    def remember(
        self,
        content: str,
        kind: MemoryKind = "semantic",
        *,
        key: str | None = None,
        source: str = "chimera",
    ) -> tuple[str, MemoryItem]:
        """ADD a new fact, UPDATE an existing one (same key), or NOOP a duplicate.

        Returns the operation name and the resulting item.
        """
        duplicate = self._find_duplicate(content, key)
        if duplicate is None:
            return "ADD", self.add(content, kind, key=key, source=source)
        if _normalize(duplicate.content) == _normalize(content):
            return "NOOP", duplicate
        return "UPDATE", self.update(duplicate.id, content)

    def merge(self, items: list[MemoryItem]) -> dict[str, int]:
        """Merge a batch, deduping against existing memory. Returns op counts."""
        counts = {"ADD": 0, "UPDATE": 0, "NOOP": 0}
        for item in items:
            op, _ = self.remember(item.content, item.kind, key=item.key, source=item.source)
            counts[op] += 1
        _log.debug("merged %d items: %s", len(items), counts)
        return counts

    def prune(self, max_items: int) -> int:
        """Keep the ``max_items`` highest-value memories; remove the rest. Returns count.

        Value is the multi-factor model in :mod:`chimera.memory.value` (recency,
        specificity, kind, curation, reliability) — not a single cue.
        """
        from chimera.memory.value import rank

        items = self.store.all()
        if len(items) <= max_items:
            return 0
        for _, item in rank(items)[max_items:]:
            self.store.remove(item.id)
        return len(items) - max_items

    def search(self, query: str, *, k: int = 5) -> list[MemoryItem]:
        """Retrieve relevant memories — full-text if the backend supports it, else keyword."""
        backend_search = getattr(self.store, "search", None)
        if callable(backend_search):  # e.g. the SQLite/FTS5 store
            result: list[MemoryItem] = backend_search(query, k=k)
            return result
        terms = set(_TOKEN.findall(query.lower()))
        if not terms:
            return []
        scored: list[tuple[int, str, MemoryItem]] = []
        for item in self.store.all():
            haystack = set(_TOKEN.findall(item.content.lower()))
            score = len(terms & haystack)
            if score:
                scored.append((score, item.id, item))
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        return [item for _, _, item in scored[:k]]
