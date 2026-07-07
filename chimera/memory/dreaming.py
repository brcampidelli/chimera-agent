"""Dreaming promotion gate (M15-C3) — promote a recall to durable memory only when it earns it.

OpenClaw's memory "dreaming" promotes a short-term recall to durable ``MEMORY.md`` only when it
clears weighted thresholds — a minimum score, a minimum recall count, enough *distinct* queries that
retrieved it, and recency/age bounds. nanobot's rule is the honesty half: advance on *measured*
signals, never on the model's say-so. This is the Chimera version: the promotion decision is derived
from real usage counters (how often and how diversely a memory was actually recalled), not from an
LLM deciding a fact "feels important".

Pure and deterministic — the weights and thresholds are the audit trail for what gets promoted, and
the decision carries the reasons it passed or failed. This complements ``value.py`` (which ranks for
*pruning* under a budget): dreaming decides *promotion* to the durable layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from chimera.memory.models import MemoryItem


@dataclass
class RecallStats:
    """The measured usage signals for one memory, over its lifetime."""

    recall_count: int = 0  # times it was retrieved into a run
    unique_queries: int = 0  # distinct queries that retrieved it (diversity of usefulness)
    age_days: float = 0.0  # how long ago it was created
    days_since_recall: float = 0.0  # recency of the last retrieval


@dataclass
class PromotionGate:
    """Weighted thresholds that decide whether a recall is promoted to durable memory."""

    min_score: float = 0.5
    min_recall_count: int = 3
    min_unique_queries: int = 2
    recency_half_life_days: float = 14.0
    max_age_days: float = 365.0
    # normalization targets (a count at/above the target contributes its full weight)
    recall_target: int = 10
    query_target: int = 5
    w_recall: float = 0.4
    w_queries: float = 0.35
    w_recency: float = 0.25

    def score(self, stats: RecallStats) -> float:
        """A weighted [0, 1] score: how much a memory has proven its worth by being used."""
        recall = min(stats.recall_count / self.recall_target, 1.0) if self.recall_target else 0.0
        queries = min(stats.unique_queries / self.query_target, 1.0) if self.query_target else 0.0
        recency = 0.5 ** (stats.days_since_recall / self.recency_half_life_days) if self.recency_half_life_days else 0.0
        return round(self.w_recall * recall + self.w_queries * queries + self.w_recency * recency, 4)


@dataclass
class PromotionDecision:
    """The outcome of a promotion check: promote or not, the score, and the reasons."""

    promote: bool
    score: float
    reasons: list[str]


def should_promote(stats: RecallStats, gate: PromotionGate | None = None) -> PromotionDecision:
    """Decide whether a recall clears the gate — score AND the hard usage/age thresholds."""
    g = gate or PromotionGate()
    score = g.score(stats)
    reasons: list[str] = []
    if stats.recall_count < g.min_recall_count:
        reasons.append(f"recalled {stats.recall_count}x < {g.min_recall_count}")
    if stats.unique_queries < g.min_unique_queries:
        reasons.append(f"{stats.unique_queries} distinct queries < {g.min_unique_queries}")
    if stats.age_days > g.max_age_days:
        reasons.append(f"stale ({stats.age_days:.0f}d > {g.max_age_days:.0f}d)")
    if score < g.min_score:
        reasons.append(f"score {score:.2f} < {g.min_score:.2f}")
    promote = not reasons
    if promote:
        reasons.append(f"earned it (score {score:.2f}, recalled {stats.recall_count}x)")
    return PromotionDecision(promote=promote, score=score, reasons=reasons)


def stats_from_item(item: MemoryItem, *, now_day: float = 0.0) -> RecallStats:
    """Read usage counters from a memory item's metadata (machine-derived, never self-reported).

    Expects ``metadata`` keys ``recall_count``, ``unique_queries``, ``created_day``,
    ``last_recall_day`` (all optional, default 0). ``now_day`` is the current day index; ages are
    computed against it, so the caller supplies the clock and the gate stays pure.
    """
    md = item.metadata
    created = float(md.get("created_day", 0.0))
    last_recall = float(md.get("last_recall_day", created))
    return RecallStats(
        recall_count=int(md.get("recall_count", 0)),
        unique_queries=int(md.get("unique_queries", 0)),
        age_days=max(0.0, now_day - created),
        days_since_recall=max(0.0, now_day - last_recall),
    )
