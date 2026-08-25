"""LLM-assisted memory consolidation — compress clusters of similar facts into one.

Pruning by value drops low-value memories; consolidation instead *merges* near-duplicate
facts into a single, richer one, cutting bloat while preserving specifics. Clustering is a
pure, deterministic token-Jaccard grouping; the summariser is injected (a
``list[str] -> str`` callable), so the logic is testable without a model. :func:`model_summarizer`
supplies the real, model-backed one. A merge is a write, so it's opt-in (``memory consolidate``).
"""

from __future__ import annotations

from collections.abc import Callable

from chimera.memory.models import MemoryItem

Summarizer = Callable[[list[str]], str]

#: Below this many characters a token is a fragment or a function word, and Jaccard overlap on
#: those measures grammar rather than meaning. It does NOT apply to scripts where one character is
#: a word: `tokens` splits Han per character, and a two-character rule would discard every one of
#: them — which is how Chinese and Russian memories came to never cluster at all.
_MIN_LENGTH = 3


def _tokens(text: str) -> set[str]:
    """Significant tokens, through the shared tokenizer.

    This module had its own ``[a-z0-9]+``, so two nearly identical Russian facts produced two empty
    sets and `_similar` refused them both. Measured: Portuguese and English clustered, Russian and
    Chinese never did — consolidation was inert in those languages rather than merely worse.
    """
    from chimera.memory.tokens import tokens as _shared

    return {t for t in _shared(text) if len(t) >= _MIN_LENGTH or not t.isascii()}


def _similar(a: str, b: str, threshold: float) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold  # Jaccard overlap


def group_similar(texts: list[str], *, threshold: float = 0.5) -> list[list[int]]:
    """Greedily group *indices* of texts whose content is similar (Jaccard >= threshold).

    A generic clustering primitive reused by memory consolidation and skill nudges.
    """
    groups: list[list[int]] = []
    reps: list[str] = []  # one representative text per group
    for index, text in enumerate(texts):
        for group_index, rep in enumerate(reps):
            if _similar(text, rep, threshold):
                groups[group_index].append(index)
                break
        else:
            groups.append([index])
            reps.append(text)
    return groups


def cluster(items: list[MemoryItem], *, threshold: float = 0.5) -> list[list[MemoryItem]]:
    """Greedily group memories whose content is similar (Jaccard >= threshold)."""
    groups = group_similar([item.content for item in items], threshold=threshold)
    return [[items[i] for i in group] for group in groups]


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
