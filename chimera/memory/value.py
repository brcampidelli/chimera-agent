"""Multi-factor memory value model + budget pruning (2606.12945).

Forgetting under a fixed budget is decided at consolidation time, before future queries
are known — so a single cue (recency or similarity alone) is misspecified. ``value()``
scores each item by a weighted sum of interpretable factors (recency, specificity, kind,
curation, reliability); ``prune`` keeps the highest-value items under a budget. Fully
deterministic and interpretable — the factor weights are the audit trail for what is kept.
"""

from __future__ import annotations

from dataclasses import dataclass

from chimera.memory.models import MemoryItem

_KIND_WEIGHT = {"persona": 1.0, "semantic": 0.8, "episodic": 0.5, "working": 0.2}
_RELIABLE_SOURCES = {"user", "hermes", "openclaw"}  # human / migrated > agent-derived


@dataclass
class ValueWeights:
    recency: float = 0.30
    specificity: float = 0.20
    kind: float = 0.25
    curation: float = 0.15
    reliability: float = 0.10


def _specificity(content: str) -> float:
    return min(len(content) / 200.0, 1.0)


def value(item: MemoryItem, *, recency: float = 1.0, weights: ValueWeights | None = None) -> float:
    """Score a memory item; higher = more worth keeping."""
    w = weights or ValueWeights()
    return (
        w.recency * recency
        + w.specificity * _specificity(item.content)
        + w.kind * _KIND_WEIGHT.get(item.kind, 0.5)
        + w.curation * (1.0 if item.key else 0.0)
        + w.reliability * (1.0 if item.source in _RELIABLE_SOURCES else 0.4)
    )


def rank(
    items: list[MemoryItem], *, weights: ValueWeights | None = None
) -> list[tuple[float, MemoryItem]]:
    """Rank items by value, most valuable first. Position is the recency proxy."""
    n = len(items)
    scored = [
        (value(item, recency=(i + 1) / n if n else 0.0, weights=weights), item)
        for i, item in enumerate(items)
    ]
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return scored
