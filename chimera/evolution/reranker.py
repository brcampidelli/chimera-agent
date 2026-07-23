"""Rank candidate solutions by P(success) from the agent's OWN past outcomes (#2).

The Decision-Transformer study's one transferable primitive: not a return-conditioned policy (wrong
shape for a coding agent — sparse terminal reward, scarce data, an already-strong pretrained model),
but an *outcome-conditioned scorer* trained on nothing — the labels Chimera already logs. For a new
``(task, candidate)`` it asks the trajectory log a k-NN question: *have solutions like this, on tasks
like this, worked before?* and returns a similarity-weighted success rate in ``[0, 1]``.

This is **inference-time and trains no weights** (the project's stated stance). Similarity comes in two
flavours: the default **lexical** token overlap (the same recall the rest of the flywheel uses today —
and, honestly, the same limit run 4 exposed), or **semantic** cosine over embeddings when an
``embed`` function is supplied (reusing ``chimera.memory.semantic``), so a paraphrased task/solution
still matches. Which one actually discriminates success from failure is an empirical question —
measure it with ``chimera.eval.reranker_ab`` before wiring the reranker into any hot path. Cold start
(no similar past outcome) returns a neutral ``prior`` so the reranker never overrides a decision it has
no evidence about.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Protocol

from chimera.memory.semantic import EmbedFn, cosine

_TOKEN = re.compile(r"[a-z0-9]+")

# lexical similarity(query_tokens, item_tokens) -> [0, 1]. Injectable (only used in lexical mode).
Similarity = Callable[[frozenset[str], frozenset[str]], float]


class _HasOutcome(Protocol):
    prompt: str
    response: str
    outcome: str  # "success" | "failure" | "unknown"


def _tokens(text: str) -> frozenset[str]:
    return frozenset(_TOKEN.findall(text.lower()))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cached_embed(embed: EmbedFn) -> EmbedFn:
    """Wrap an embedder with a per-text cache so each distinct text is embedded exactly once.

    Lets a caller (e.g. the reranker A/B's leave-one-out loop) construct many rerankers over
    overlapping corpora without paying to re-embed the same text each fold.
    """
    cache: dict[str, list[float]] = {}

    def wrapped(texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in cache]
        if missing:
            for text, vec in zip(missing, embed(missing), strict=True):
                cache[text] = vec
        return [cache[t] for t in texts]

    return wrapped


class SuccessReranker:
    """k-NN P(success) scorer over past ``(task, candidate) -> outcome`` records. Trains nothing.

    Lexical by default; pass ``embed`` for semantic cosine ranking. Constructing a semantic reranker
    embeds the history once (one batched call).
    """

    def __init__(
        self,
        history: Iterable[tuple[str, bool]],
        *,
        k: int = 5,
        prior: float = 0.5,
        similarity: Similarity | None = None,
        embed: EmbedFn | None = None,
    ) -> None:
        raw = [(str(text), bool(success)) for text, success in history if text]
        self.k = k
        self.prior = prior
        self._embed = embed
        self._successes = [s for _, s in raw]
        if embed is None:
            self._similarity: Similarity | None = similarity or _jaccard
            self._tokens: list[frozenset[str]] | None = [_tokens(t) for t, _ in raw]
            self._vecs: list[list[float]] | None = None
        else:
            self._similarity = None
            self._tokens = None
            self._vecs = list(embed([t for t, _ in raw])) if raw else []

    @classmethod
    def from_trajectories(
        cls, trajectories: Iterable[_HasOutcome], **kwargs: object
    ) -> SuccessReranker:
        """Build from the trajectory log — each verified success/failure becomes a labelled record.

        ``unknown`` outcomes are skipped (no label). The record text is task + solution so a new
        candidate is matched on both what was asked and what was tried.
        """
        history: list[tuple[str, bool]] = [
            (f"{t.prompt}\n{t.response}", t.outcome == "success")
            for t in trajectories
            if getattr(t, "outcome", "unknown") in ("success", "failure")
        ]
        return cls(history, **kwargs)  # type: ignore[arg-type]

    def score(self, task: str, candidate: str) -> float:
        """P(success) for solving ``task`` with ``candidate``, from the k most similar past outcomes."""
        sims = self._similarities(f"{task}\n{candidate}")
        if not sims:
            return self.prior
        top = sorted(sims, key=lambda pair: pair[0], reverse=True)[: self.k]
        top = [(sim, success) for sim, success in top if sim > 0.0]
        if not top:  # nothing genuinely similar -> no evidence -> neutral prior
            return self.prior
        weight = sum(sim for sim, _ in top)
        hit = sum(sim for sim, success in top if success)
        return hit / weight if weight else self.prior

    def _similarities(self, query: str) -> list[tuple[float, bool]]:
        """(similarity, success) of every history record to ``query``; [] when there is no signal."""
        if self._embed is None:
            if not self._tokens:
                return []
            qtok = _tokens(query)
            if not qtok:
                return []
            return [(self._similarity(qtok, toks), s) for toks, s in zip(self._tokens, self._successes, strict=True)]  # type: ignore[misc]
        if not self._vecs:
            return []
        qvec = self._embed([query])[0]
        return [(cosine(qvec, vec), s) for vec, s in zip(self._vecs, self._successes, strict=True)]

    def rerank(self, task: str, candidates: Iterable[str]) -> list[tuple[str, float]]:
        """Candidates paired with their P(success), best first (stable on ties by input order)."""
        scored = [(c, self.score(task, c)) for c in candidates]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def best(self, task: str, candidates: Iterable[str]) -> str | None:
        """The highest-scoring candidate, or None if there are none."""
        ranked = self.rerank(task, candidates)
        return ranked[0][0] if ranked else None


__all__ = ["Similarity", "SuccessReranker", "cached_embed"]
