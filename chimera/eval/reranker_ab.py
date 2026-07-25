"""Does the success reranker actually discriminate — and does semantic beat lexical? (measure #2).

Before wiring the ``SuccessReranker`` into any hot path, answer the empirical question run 4 left
open: can a k-NN P(success) scorer, built from the agent's own labelled ``(task, candidate) ->
outcome`` records, tell a would-be success from a would-be failure — and is embedding-cosine recall
(the semantic path the SourceForge/sqlite-vec study points at) better than lexical token overlap at it?

The metric is **AUC** (a.k.a. the concordance / Mann–Whitney statistic): the probability that a random
true-success record is scored above a random true-failure record. 0.5 = no discrimination (a coin);
1.0 = perfect separation. It is threshold-free, so it measures the reranker's *ranking* quality
directly. Scores are produced **leave-one-out**: each record is scored by a reranker built from all the
others, so no record predicts itself.

The embedder is injected, so this is testable with a deterministic fake and, in production, uses the
same gateway embedder as semantic memory. Embedding is done once per distinct text (``cached_embed``),
so the O(n²) leave-one-out loop still costs only n embed calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from chimera.evolution.reranker import SuccessReranker, cached_embed
from chimera.memory.semantic import EmbedFn

# One labelled record: (task prompt, the candidate solution text, did it succeed).
Record = tuple[str, str, bool]


def auc(scored: list[tuple[float, bool]]) -> float:
    """Probability a true-success outranks a true-failure (ties count as ½). 0.5 with no pos/neg pair."""
    pos = [s for s, y in scored if y]
    neg = [s for s, y in scored if not y]
    if not pos or not neg:
        return 0.5
    wins = sum((1.0 if p > n else 0.5 if p == n else 0.0) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


@dataclass
class RerankerABReport:
    n: int
    successes: int
    lexical_auc: float
    semantic_auc: float | None  # None when no embedder was supplied
    k: int

    @property
    def gap(self) -> float | None:
        """semantic − lexical AUC, or None without a semantic run."""
        return None if self.semantic_auc is None else self.semantic_auc - self.lexical_auc

    def summary(self) -> dict[str, object]:
        return {
            "n": self.n,
            "successes": self.successes,
            "lexical_auc": round(self.lexical_auc, 4),
            "semantic_auc": None if self.semantic_auc is None else round(self.semantic_auc, 4),
            "gap": None if self.gap is None else round(self.gap, 4),
            "k": self.k,
        }


def _loo_scores(corpus: list[Record], *, k: int, embed: EmbedFn | None) -> list[tuple[float, bool]]:
    """Leave-one-out P(success) for every record, from a reranker built on all the others."""
    out: list[tuple[float, bool]] = []
    for i, (task, candidate, success) in enumerate(corpus):
        history = [(f"{t}\n{c}", s) for j, (t, c, s) in enumerate(corpus) if j != i]
        reranker = SuccessReranker(history, k=k, embed=embed)
        out.append((reranker.score(task, candidate), success))
    return out


def run_reranker_ab(corpus: list[Record], *, embed: EmbedFn | None = None, k: int = 5) -> RerankerABReport:
    """Leave-one-out AUC of the reranker on ``corpus`` — lexical always, semantic when ``embed`` given."""
    lexical = auc(_loo_scores(corpus, k=k, embed=None))
    semantic: float | None = None
    if embed is not None:
        # One cache shared across all folds, so each distinct text is embedded exactly once.
        semantic = auc(_loo_scores(corpus, k=k, embed=cached_embed(embed)))
    return RerankerABReport(
        n=len(corpus),
        successes=sum(1 for _, _, s in corpus if s),
        lexical_auc=lexical,
        semantic_auc=semantic,
        k=k,
    )


def format_report(report: RerankerABReport) -> str:
    lines = [
        f"reranker A/B  n={report.n} ({report.successes} success / {report.n - report.successes} fail)  k={report.k}",
        f"  lexical   AUC {report.lexical_auc:.3f}"
        + ("  (0.5 = no discrimination)" if abs(report.lexical_auc - 0.5) < 0.02 else ""),
    ]
    if report.semantic_auc is not None:
        lines.append(f"  semantic  AUC {report.semantic_auc:.3f}   gap {report.gap:+.3f} vs lexical")
    return "\n".join(lines)


__all__ = ["Record", "RerankerABReport", "auc", "format_report", "run_reranker_ab"]
