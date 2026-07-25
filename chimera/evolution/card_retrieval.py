"""BM25 retrieval + injection of TRS reasoning cards (Improvement #1).

Closes the learn->use loop: learned skill cards are retrieved for the current task and
injected into the reasoning context. Retrieval is BM25 over name + description +
triggers via an in-memory SQLite FTS5 index — the configuration the TRS paper found best
for reasoning (dense embeddings retrieved structurally-mismatched hints that misled) —
with a zero-dependency token-overlap fallback when FTS5 is not compiled in.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore
from chimera.memory.semantic import EmbedFn, cosine

_log = logging.getLogger(__name__)
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

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CardIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def search(self, query: str, k: int = 3, *, min_overlap: int = 0) -> list[LearnedSkill]:
        """Top-``k`` cards for ``query``. ``min_overlap`` gates on relevance: a returned card is kept
        only if it shares at least that many query terms — so a task with no strong match returns []
        (injects nothing, pays no tokens) instead of dragging in weakly-matched, misleading cards."""
        terms = _TOKEN.findall(query.lower())
        if not terms or not self._cards:
            return []
        query_terms = set(terms)
        ranked = self._ranked(terms, query_terms, k)
        if min_overlap <= 0:
            return ranked
        return [c for c in ranked if self._overlap(query_terms, c) >= min_overlap]

    def _ranked(self, terms: list[str], query_terms: set[str], k: int) -> list[LearnedSkill]:
        if self._fts:
            match = " OR ".join(terms)
            try:
                # Secondary sort on name so tied BM25 ranks resolve deterministically — otherwise which
                # card lands in the top-k (and gets injected + credited a use/success) could flip
                # between runs, matching the deterministic token-overlap fallback below.
                rows = self._conn.execute(
                    "SELECT name FROM cards WHERE doc MATCH ? ORDER BY rank, name LIMIT ?", (match, k)
                ).fetchall()
                return [self._cards[str(r[0])] for r in rows if str(r[0]) in self._cards]
            except sqlite3.OperationalError:
                pass  # malformed FTS query — fall through to token overlap
        return self._fallback(query_terms, k)

    @staticmethod
    def _overlap(query_terms: set[str], card: LearnedSkill) -> int:
        return len(query_terms & set(_TOKEN.findall(_doc(card).lower())))

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

    def __init__(
        self,
        store: SkillStore,
        *,
        k: int = 3,
        min_overlap: int = 0,
        max_lines: int = 4,
        embed: EmbedFn | None = None,
        min_similarity: float = 0.3,
    ) -> None:
        self.store = store
        self.k = k
        self.min_overlap = min_overlap  # lexical relevance gate: 0 = inject any match; higher = strong only
        self.max_lines = max_lines  # render budget per card (fewer lines = cheaper injection)
        # Semantic recall (memory_bench proved keyword misses paraphrases 0% -> semantic 94%): when an
        # embedder is supplied, rank cards by embedding cosine instead of BM25, so a card matches a task
        # by MEANING not shared tokens. ``min_similarity`` is the cosine floor (the semantic analogue of
        # ``min_overlap``) so a weak match injects nothing rather than dragging in noise.
        self._embed = embed
        self.min_similarity = min_similarity
        self.last_retrieved: list[str] = []

    def card_context(self, task: str) -> str:
        # Retrievable = active + provisional (M18-4): a provisional skill runs so it earns a measured
        # track record for the lifecycle policy. A pending (possibly poisoned) or retired skill stays
        # out until a human approves / it recovers.
        cards = self.store.retrievable()
        if not cards:
            self.last_retrieved = []
            return ""
        hits: list[LearnedSkill] | None = None
        if self._embed is not None:
            try:
                hits = self._semantic_hits(cards, task)
            except Exception as exc:  # noqa: BLE001 — never hard-fail a run because embeddings are down
                _log.warning("semantic card recall failed, falling back to BM25: %s", exc)
        if hits is None:
            with CardIndex(cards) as index:  # close the in-memory SQLite connection after the search
                hits = index.search(task, k=self.k, min_overlap=self.min_overlap)
        self.last_retrieved = [card.name for card in hits]
        return cards_context_block(hits, max_lines_per_card=self.max_lines)

    def _semantic_hits(self, cards: list[LearnedSkill], task: str) -> list[LearnedSkill]:
        """Top-k cards by embedding cosine to the task, gated by the ``min_similarity`` floor."""
        embed = self._embed
        assert embed is not None
        vecs = embed([_doc(card) for card in cards] + [task])
        query_vec = vecs[-1]
        scored = sorted(
            ((cosine(query_vec, vec), card) for card, vec in zip(cards, vecs[:-1], strict=True)),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [card for sim, card in scored[: self.k] if sim >= self.min_similarity]

    def record_outcome(self, success: bool) -> None:
        """Credit the run's outcome to the cards that were injected into it.

        Per-skill telemetry (uses / successes) is what makes retirement a measured
        decision instead of a guess — a skill that is retrieved often but never moves
        outcomes surfaces in ``chimera skills-stats``.
        """
        for name in self.last_retrieved:
            self.store.record_use(name, success=success)
        self.last_retrieved = []
