"""BM25 retrieval + injection of TRS reasoning cards (Improvement #1).

Closes the learn->use loop: learned skill cards are retrieved for the current task and
injected into the reasoning context. Retrieval is BM25 over name + description +
triggers via an in-memory SQLite FTS5 index — the configuration the TRS paper found best
for reasoning (dense embeddings retrieved structurally-mismatched hints that misled) —
with a zero-dependency token-overlap fallback when FTS5 is not compiled in.
"""

from __future__ import annotations

import re
import sqlite3

from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore

_TOKEN = re.compile(r"[a-z0-9]+")
_INSTRUCTION = (
    "Prefer the most directly applicable skill; ignore irrelevant or contradictory "
    "advice. Keep reasoning concise while maintaining correctness."
)


def _doc(card: LearnedSkill) -> str:
    return f"{card.name} {card.description} {' '.join(card.triggers)}"


class CardIndex:
    """A small BM25 index over a set of learned skill cards (FTS5, token-overlap fallback)."""

    def __init__(self, cards: list[LearnedSkill]) -> None:
        self._cards = {c.name: c for c in cards}
        self._conn = sqlite3.connect(":memory:")
        self._fts = self._build()

    def _build(self) -> bool:
        try:
            self._conn.execute("CREATE VIRTUAL TABLE cards USING fts5(name UNINDEXED, doc)")
            self._conn.executemany(
                "INSERT INTO cards(name, doc) VALUES (?, ?)",
                [(c.name, _doc(c)) for c in self._cards.values()],
            )
            self._conn.commit()
            return True
        except sqlite3.OperationalError:  # FTS5 not compiled in — use the fallback scorer
            return False

    def search(self, query: str, k: int = 3) -> list[LearnedSkill]:
        terms = _TOKEN.findall(query.lower())
        if not terms or not self._cards:
            return []
        if self._fts:
            match = " OR ".join(terms)
            try:
                rows = self._conn.execute(
                    "SELECT name FROM cards WHERE doc MATCH ? ORDER BY rank LIMIT ?", (match, k)
                ).fetchall()
                return [self._cards[str(r[0])] for r in rows if str(r[0]) in self._cards]
            except sqlite3.OperationalError:
                pass  # malformed FTS query — fall through to token overlap
        return self._fallback(set(terms), k)

    def _fallback(self, terms: set[str], k: int) -> list[LearnedSkill]:
        scored: list[tuple[int, str, LearnedSkill]] = []
        for card in self._cards.values():
            overlap = len(terms & set(_TOKEN.findall(_doc(card).lower())))
            if overlap:
                scored.append((overlap, card.name, card))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [card for _, _, card in scored[:k]]


def cards_context_block(cards: list[LearnedSkill], *, max_lines_per_card: int = 4) -> str:
    """Render retrieved cards as a TRS injection block (budget-capped)."""
    if not cards:
        return ""
    parts = ["Retrieved reasoning skills:"]
    for card in cards:
        tag = " (anti-pattern)" if card.kind == "anti_pattern" else ""
        parts.append(f"[{card.name}]{tag}\n{card.card_text(max_lines=max_lines_per_card)}")
    parts.append(_INSTRUCTION)
    return "\n\n".join(parts)


class CardRetriever:
    """Retrieves and formats skill-card context for a task from a SkillStore."""

    def __init__(self, store: SkillStore, *, k: int = 3) -> None:
        self.store = store
        self.k = k
        self.last_retrieved: list[str] = []

    def card_context(self, task: str) -> str:
        # Retrievable = active + provisional (M18-4): a provisional skill runs so it earns a measured
        # track record for the lifecycle policy. A pending (possibly poisoned) or retired skill stays
        # out until a human approves / it recovers.
        cards = self.store.retrievable()
        if not cards:
            self.last_retrieved = []
            return ""
        hits = CardIndex(cards).search(task, k=self.k)
        self.last_retrieved = [card.name for card in hits]
        return cards_context_block(hits)

    def record_outcome(self, success: bool) -> None:
        """Credit the run's outcome to the cards that were injected into it.

        Per-skill telemetry (uses / successes) is what makes retirement a measured
        decision instead of a guess — a skill that is retrieved often but never moves
        outcomes surfaces in ``chimera skills-stats``.
        """
        for name in self.last_retrieved:
            self.store.record_use(name, success=success)
        self.last_retrieved = []
