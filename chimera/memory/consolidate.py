"""LLM-assisted memory consolidation — compress clusters of similar facts into one.

Pruning by value drops low-value memories; consolidation instead *merges* near-duplicate
facts into a single, richer one, cutting bloat while preserving specifics. Clustering is a
pure, deterministic token-Jaccard grouping; the summariser is injected (a
``list[str] -> str`` callable), so the logic is testable without a model. :func:`model_summarizer`
supplies the real, model-backed one. A merge is a write, so it's opt-in (``memory consolidate``).
"""

from __future__ import annotations

import re
from collections.abc import Callable

from chimera.memory.models import MemoryItem

_TOKEN = re.compile(r"[a-z0-9]+")
Summarizer = Callable[[list[str]], str]


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.lower()) if len(token) >= 3}


def _similar(a: str, b: str, threshold: float) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold  # Jaccard overlap


def cluster(items: list[MemoryItem], *, threshold: float = 0.5) -> list[list[MemoryItem]]:
    """Greedily group memories whose content is similar (Jaccard >= threshold)."""
    clusters: list[list[MemoryItem]] = []
    for item in items:
        for group in clusters:
            if _similar(item.content, group[0].content, threshold):
                group.append(item)
                break
        else:
            clusters.append([item])
    return clusters


def model_summarizer(backend: object, model: str | None = None) -> Summarizer:
    """A summariser that asks a model to merge related facts into one concise fact."""

    def summarize(facts: list[str]) -> str:
        from chimera.providers.gateway import Message

        joined = "\n".join(f"- {fact}" for fact in facts)
        prompt = (
            f"Merge these related facts into ONE concise fact, preserving every specific "
            f"detail (names, numbers, preferences):\n{joined}\n\nReply with only the merged fact."
        )
        reply = backend.complete(  # type: ignore[attr-defined]
            [Message(role="user", content=prompt)], model=model, temperature=0.2
        )
        merged: str = reply.content.strip()
        return merged

    return summarize
