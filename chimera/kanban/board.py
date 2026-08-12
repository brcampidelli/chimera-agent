"""A JSON-backed Kanban board."""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from chimera.kanban.models import Column, KanbanCard
from chimera.telemetry import get_logger

_log = get_logger("kanban.board")


class KanbanBoard:
    """A JSON-file board of cards, addressable by id and groupable by column.

    Mutations are serialised by a lock. Every one of them ends in :meth:`save`, which rewrites the
    whole file — so two threads finishing a card at the same moment can interleave a read of
    ``_cards`` with another thread's write and persist a board missing somebody's result. That was
    unreachable while dispatch ran one card at a time; it stops being unreachable the moment it
    runs several, and a lost result on a board is a task that silently never happened.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cards: dict[str, KanbanCard] = {}
        # Re-entrant: the public mutators call `save()`, which takes it again.
        self._lock = threading.RLock()
        self.load()

    def load(self) -> None:
        self._cards = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            # Skip a single malformed card (schema drift, hand-edit) instead of aborting the whole
            # load — one bad entry must not make every board command crash.
            try:
                card = KanbanCard.model_validate(item)
            except ValueError as exc:
                _log.warning("skipping malformed kanban card: %s", exc)
                continue
            self._cards[card.id] = card

    def save(self) -> None:
        with self._lock:
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [card.model_dump() for card in self._cards.values()]
        # Atomic write: a crash mid-write must not truncate the board and lose every card.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(
        self, title: str, action: str, *, lane: str = "solve", verify: str | None = None
    ) -> KanbanCard:
        card = KanbanCard(
            id=uuid.uuid4().hex[:8], title=title, action=action, lane=lane, verify=verify
        )
        with self._lock:
            self._cards[card.id] = card
            self._save_locked()
        return card

    def get(self, card_id: str) -> KanbanCard | None:
        return self._cards.get(card_id)

    def cards(self, column: Column | None = None) -> list[KanbanCard]:
        # Snapshot under the lock: iterating the live dict while another thread finishes a card
        # raises "dictionary changed size during iteration" — a crash in the reader, caused by a
        # writer, in a place with nothing to do with either.
        with self._lock:
            items = list(self._cards.values())
        return [c for c in items if c.column == column] if column else items

    def move(self, card_id: str, column: Column) -> KanbanCard:
        with self._lock:
            card = self._cards[card_id]
            card.column = column
            self._save_locked()
            return card

    def record_result(self, card_id: str, *, success: bool, result: str) -> KanbanCard:
        with self._lock:
            card = self._cards[card_id]
            card.success = success
            card.result = result
            self._save_locked()
            return card

    def remove(self, card_id: str) -> bool:
        with self._lock:
            existed = card_id in self._cards
            self._cards.pop(card_id, None)
            if existed:
                self._save_locked()
        return existed

    def __len__(self) -> int:
        return len(self._cards)
