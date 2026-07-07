"""Tests for the dreaming promotion gate (M15-C3)."""

from __future__ import annotations

from chimera.memory.dreaming import (
    PromotionGate,
    RecallStats,
    should_promote,
    stats_from_item,
)
from chimera.memory.models import MemoryItem


def test_well_used_recall_is_promoted() -> None:
    stats = RecallStats(recall_count=8, unique_queries=4, age_days=10, days_since_recall=1)
    decision = should_promote(stats)
    assert decision.promote is True
    assert decision.score >= 0.5
    assert "earned it" in decision.reasons[-1]


def test_rarely_recalled_is_rejected() -> None:
    stats = RecallStats(recall_count=1, unique_queries=1, age_days=2, days_since_recall=1)
    decision = should_promote(stats)
    assert decision.promote is False
    assert any("recalled 1x" in r for r in decision.reasons)


def test_single_query_diversity_gate() -> None:
    # Recalled many times but always by the SAME query → not broadly useful.
    stats = RecallStats(recall_count=20, unique_queries=1, age_days=5, days_since_recall=0)
    decision = should_promote(stats)
    assert decision.promote is False
    assert any("distinct queries" in r for r in decision.reasons)


def test_stale_memory_is_rejected() -> None:
    stats = RecallStats(recall_count=9, unique_queries=4, age_days=400, days_since_recall=0)
    decision = should_promote(stats, PromotionGate(max_age_days=365))
    assert decision.promote is False
    assert any("stale" in r for r in decision.reasons)


def test_recency_half_life_decays_the_score() -> None:
    gate = PromotionGate(recency_half_life_days=14)
    fresh = gate.score(RecallStats(recall_count=5, unique_queries=3, days_since_recall=0))
    old = gate.score(RecallStats(recall_count=5, unique_queries=3, days_since_recall=28))  # 2 half-lives
    assert fresh > old  # a long-unused memory scores lower even with the same counts


def test_score_is_bounded_and_weighted() -> None:
    gate = PromotionGate()
    maxed = gate.score(RecallStats(recall_count=100, unique_queries=100, days_since_recall=0))
    assert maxed <= 1.0  # normalized targets cap each factor's contribution
    # A never-used memory only earns the recency term (days_since_recall=0), never the usage terms.
    unused = gate.score(RecallStats())
    assert unused <= gate.w_recency


# --- stats extracted from a memory item's metadata (machine-derived) ----------------------


def test_stats_from_item_reads_metadata() -> None:
    item = MemoryItem(
        id="m1", content="a useful fact",
        metadata={"recall_count": 6, "unique_queries": 3, "created_day": 2, "last_recall_day": 9},
    )
    stats = stats_from_item(item, now_day=12)
    assert stats.recall_count == 6 and stats.unique_queries == 3
    assert stats.age_days == 10  # 12 - 2
    assert stats.days_since_recall == 3  # 12 - 9


def test_stats_from_item_defaults_are_zero() -> None:
    stats = stats_from_item(MemoryItem(id="m", content="x"), now_day=5)
    assert stats.recall_count == 0 and stats.unique_queries == 0
    # No created_day -> defaults to day 0, so age = now_day - 0 = 5; last_recall defaults to created.
    assert stats.age_days == 5.0
    assert should_promote(stats).promote is False  # an unused memory is never promoted


def test_end_to_end_item_promotion() -> None:
    item = MemoryItem(
        id="m", content="frequently recalled, diverse queries",
        metadata={"recall_count": 12, "unique_queries": 5, "created_day": 0, "last_recall_day": 20},
    )
    assert should_promote(stats_from_item(item, now_day=21)).promote is True
