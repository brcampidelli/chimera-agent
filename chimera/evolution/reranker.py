"""Rank candidate solutions by P(success) from the agent's OWN past outcomes (#2).

The Decision-Transformer study's one transferable primitive: not a return-conditioned policy (wrong
shape for a coding agent — sparse terminal reward, scarce data, an already-strong pretrained model),
but an *outcome-conditioned scorer* trained on nothing — the labels Chimera already logs. For a new
``(task, candidate)`` it asks the trajectory log a k-NN question: *have solutions like this, on tasks
like this, worked before?* and returns a similarity-weighted success rate in ``[0, 1]``.

This is **inference-time and trains no weights** — the project's stated stance (``trajectory.py``:
"nothing here trains a model or changes weights automatically"). Similarity is injectable: the default
is lexical token overlap (the same recall the rest of the flywheel uses today), and the honest caveat
is that lexical recall is exactly the limit run 4 exposed — swapping in a semantic embedder (the
sqlite-vec path from the SourceForge study) is what would make the neighbours genuinely relevant.
Cold start (no similar past outcome) returns a neutral ``prior`` so the reranker never overrides a
decision it has no evidence about.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")

# similarity(query_tokens, item_tokens) -> [0, 1]. Injectable so a semantic embedder can replace it.
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


class SuccessReranker:
    """k-NN P(success) scorer over past ``(task, candidate) -> outcome`` records. Trains nothing."""

    def __init__(
        self,
        history: Iterable[tuple[str, bool]],
        *,
        k: int = 5,
        prior: float = 0.5,
        similarity: Similarity | None = None,
    ) -> None:
        # Each record: (text representing the past task+solution, whether it succeeded).
        self._items: list[tuple[frozenset[str], bool]] = [
            (_tokens(text), success) for text, success in history if text
        ]
        self.k = k
        self.prior = prior
        self._similarity = similarity or _jaccard

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
        if not self._items:
            return self.prior
        query = _tokens(f"{task}\n{candidate}")
        if not query:
            return self.prior
        sims = sorted(
            ((self._similarity(query, toks), success) for toks, success in self._items),
            key=lambda pair: pair[0],
            reverse=True,
        )
        top = [(sim, success) for sim, success in sims[: self.k] if sim > 0.0]
        if not top:  # nothing genuinely similar -> no evidence -> neutral prior
            return self.prior
        weight = sum(sim for sim, _ in top)
        hit = sum(sim for sim, success in top if success)
        return hit / weight if weight else self.prior

    def rerank(self, task: str, candidates: Iterable[str]) -> list[tuple[str, float]]:
        """Candidates paired with their P(success), best first (stable on ties by input order)."""
        scored = [(c, self.score(task, c)) for c in candidates]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored

    def best(self, task: str, candidates: Iterable[str]) -> str | None:
        """The highest-scoring candidate, or None if there are none."""
        ranked = self.rerank(task, candidates)
        return ranked[0][0] if ranked else None


__all__ = ["Similarity", "SuccessReranker"]
