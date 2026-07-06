"""Semantic recall over memory — cosine ranking on embeddings, with a content cache.

Keyword/FTS search finds a fact only when the query shares a token with it; a paraphrase
(``"physician"`` vs a fact about a ``"doctor"``) has no shared token and is missed entirely
— the exact gap :mod:`chimera.eval.memory_bench` measures. This index closes it by embedding
both fact and query into a vector space where synonyms land close together, then ranking by
cosine similarity.

The embedder is **injected** (``EmbedFn``), so this is unit-testable with a deterministic fake
and provider-agnostic in production (see :meth:`chimera.providers.gateway.LLMGateway.embed`).
Item vectors are cached by content, so repeated searches over a stable corpus embed only the
new query — one batched embed call for the corpus, one per distinct query thereafter.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from chimera.memory.models import MemoryItem

# A batched embedder: a list of texts -> a list of equal-length vectors, order-preserving.
EmbedFn = Callable[[list[str]], list[list[float]]]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors; 0.0 if either is empty or zero-norm."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticIndex:
    """Ranks memory items against a query by embedding cosine similarity.

    Stateless w.r.t. the corpus — :meth:`search` takes the live item list each call, so it
    always reflects the current store. A content-keyed cache means only unseen texts are
    embedded, so passing the same corpus repeatedly is cheap.
    """

    def __init__(self, embed: EmbedFn) -> None:
        self._embed = embed
        self._cache: dict[str, list[float]] = {}

    def _vectors(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            for text, vec in zip(missing, self._embed(missing), strict=True):
                self._cache[text] = vec
        return [self._cache[t] for t in texts]

    def search(self, query: str, items: list[MemoryItem], k: int) -> list[MemoryItem]:
        """Top-``k`` items by cosine similarity to ``query`` (empty list if no items)."""
        if not items:
            return []
        item_vecs = self._vectors([item.content for item in items])
        query_vec = self._vectors([query])[0]
        scored = sorted(
            zip(items, item_vecs, strict=True),
            key=lambda pair: cosine(query_vec, pair[1]),
            reverse=True,
        )
        return [item for item, _ in scored[:k]]
