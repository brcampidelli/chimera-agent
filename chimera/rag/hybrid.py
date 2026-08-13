"""Two retrievers, one ranking.

Keyword search and vector search fail in opposite directions, which is the entire argument for
running both. FTS finds `resolve_verify` when you type `resolve_verify` and nothing when you type
"how do we decide whether to keep the change". Embeddings do the reverse: they bridge the
paraphrase and they routinely rank a chunk that talks *about* the concept above the one that
implements it — and they cannot find a rare identifier at all, because a token seen twice in
training has no useful vector.

**Reciprocal Rank Fusion, not score blending.** A bm25 score and a cosine similarity are not
comparable numbers: one is unbounded and corpus-dependent, the other is bounded in [-1, 1]. Any
weighted sum of the two is a weight that has to be tuned per corpus and will be tuned once, by
somebody, on one repository. RRF throws the magnitudes away and keeps only the ORDER each retriever
produced, so there is nothing to calibrate and no corpus on which the arithmetic quietly inverts.

The one knob (`k`) damps how much the top of a list dominates. 60 is the value from the original
paper and there is no measurement here that would justify choosing another, so it stays as it is
until a bench says otherwise.
"""

from __future__ import annotations

from dataclasses import replace

from chimera.rag.store import Hit

#: The RRF damping constant. Larger = flatter, so a confident first place counts for less.
RRF_K = 60


def reciprocal_rank_fusion(
    lists: list[list[Hit]], *, k: int = RRF_K, limit: int = 10, prior: dict[str, float] | None = None
) -> list[Hit]:
    """Fuse ranked lists into one, by rank alone.

    ``prior`` is an optional per-path weight — the repository map's PageRank, when the caller has
    it. It is a **tiebreak only**: it orders results the fusion scored identically and can never
    reorder ones it separated.

    That is stricter than it first looks, and the strictness is the point. A first draft multiplied
    the fused score by the prior, on the reasoning that importance should be "subordinate to
    relevance" — and its own test caught that it was not. Consecutive RRF scores are 1/61 and 1/62,
    a 1.6% gap, which any prior worth having flips. So the multiplying version let a central file
    outrank an exact match, which is precisely the case retrieval is most useful for: a rare
    identifier in an unimportant file.

    A central file is not evidence that it answers this question. It is evidence that if two
    candidates are otherwise indistinguishable, one of them matters more.
    """
    scores: dict[str, float] = {}
    best: dict[str, Hit] = {}
    sources: dict[str, set[str]] = {}

    for ranked in lists:
        for rank, hit in enumerate(ranked, start=1):
            ident = hit.chunk.ident
            scores[ident] = scores.get(ident, 0.0) + 1.0 / (k + rank)
            sources.setdefault(ident, set()).add(hit.source)
            # Keep the hit whose own retriever ranked it best, so the surviving `score` is a real
            # number some retriever produced rather than a fused one nothing can explain.
            if ident not in best or rank == 1:
                best[ident] = hit

    # Sorted on (fused score, prior) so the prior is consulted only where the first key is equal.
    # Arithmetic — adding or multiplying it in — cannot express "only on a tie": whatever epsilon is
    # chosen is either big enough to reorder a real difference or small enough to be nothing.
    weights = prior or {}
    order = sorted(
        scores.items(),
        key=lambda pair: (pair[1], weights.get(best[pair[0]].chunk.path, 0.0)),
        reverse=True,
    )
    out: list[Hit] = []
    for ident, score in order[:limit]:
        found = sources.get(ident, set())
        # "hybrid" only when both retrievers actually found it — the label is evidence about how the
        # result was reached, and calling a keyword-only hit hybrid would make it useless.
        source = "hybrid" if len(found) > 1 else next(iter(found), "hybrid")
        out.append(replace(best[ident], score=score, source=source))
    return out
