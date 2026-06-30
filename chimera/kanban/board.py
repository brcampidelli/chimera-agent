"""A JSON-backed Kanban board."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from chimera.kanban.models import Column, KanbanCard


class KanbanBoard:
    """A JSON-file board of cards, addressable by id and groupable by column."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cards: dict[str, KanbanCard] = {}
        self.load()

    def load(self) -> None:
        self._cards = {}
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        for item in raw:
            card = KanbanCard.model_validate(item)
            self._cards[card.id] = card

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [card.model_dump() for card in self._cards.values()]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(
        self, title: str, action: str, *, lane: str = "solve", verify: str | None = None
    ) -> KanbanCard:
        card = KanbanCard(
            id=uuid.uuid4().hex[:8], title=title, action=action, lane=lane, verify=verify
        )
        self._cards[card.id] = card
        self.save()
        return card

    def get(self, card_id: str) -> KanbanCard | None:
        return self._cards.get(card_id)

    def cards(self, column: Column | None = None) -> list[KanbanCard]:
        items = list(self._cards.values())
        return [c for c in items if c.column == column] if column else items

    def move(self, card_id: str, column: Column) -> KanbanCard:
        card = self._cards[card_id]
        card.column = column
        self.save()
        return card

    def record_result(self, card_id: str, *, success: bool, result: str) -> KanbanCard:
        card = self._cards[card_id]
        card.success = success
        card.result = result
        self.save()
        return card

    def remove(self, card_id: str) -> bool:
        existed = card_id in self._cards
        self._cards.pop(card_id, None)
        if existed:
            self.save()
        return existed

    def __len__(self) -> int:
        return len(self._cards)
