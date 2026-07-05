"""Anti-stagnation signal for iterative improvement loops (a crowding-score analog).

Inspired by the autonomous-research-loop **crowding score** (arXiv 2606.29717): when
successive candidates keep making the *same* mistakes, the loop is circling a local
optimum and more refinement is wasted — the useful move is to **pivot** to a different
approach rather than refine. This detects that redundancy two ways, both advisory
(they only *recommend* a pivot, never block) and pure (no I/O, fully testable):

* **vector mode** — the faithful crowding-score analog: each round contributes a
  per-item *error vector* across a fixed suite (higher = worse; e.g. 1.0 = failed,
  0.0 = passed). High mean pairwise Pearson correlation across the last ``window``
  rounds means the loop keeps failing the *same items* — a stuck local optimum.
* **signature mode** — for a single-task retry loop with no multi-item vector: each
  round contributes a failure *signature* (the fault hint / verifier output). The last
  ``window`` signatures being identical means the retries keep failing the same way.

The two modes never mix in one detector: whichever was recorded is what ``assess`` uses.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from math import sqrt

from chimera.telemetry import get_logger

_log = get_logger("evolution.stagnation")

# Value above which an item counts as "failed/erred" in an error vector (higher = worse).
_FAIL_LEVEL = 0.5


def _normalize_signature(text: str) -> str:
    """Lowercase, collapse whitespace and volatile digits so equivalent faults match."""
    text = re.sub(r"\d+", "#", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()[:400]


def pearson(a: Sequence[float], b: Sequence[float]) -> float:
    """Pearson correlation of two equal-length vectors, with degenerate handling.

    A zero-variance (constant) vector has undefined Pearson correlation; here two such
    vectors count as fully correlated (1.0) when elementwise-equal — a loop that keeps
    producing the *same* constant outcome is maximally redundant — and 0.0 otherwise.
    """
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    mean_a, mean_b = sum(a) / n, sum(b) / n
    da = [x - mean_a for x in a]
    db = [y - mean_b for y in b]
    var_a, var_b = sum(x * x for x in da), sum(y * y for y in db)
    if var_a == 0.0 or var_b == 0.0:
        return 1.0 if list(a) == list(b) else 0.0
    cov = sum(x * y for x, y in zip(da, db, strict=True))
    return cov / sqrt(var_a * var_b)


def mean_pairwise_correlation(vectors: Sequence[Sequence[float]]) -> float:
    """Average Pearson correlation over every distinct pair of vectors (0.0 if < 2)."""
    n = len(vectors)
    if n < 2:
        return 0.0
    total = count = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            total += pearson(vectors[i], vectors[j])
            count += 1
    return total / count if count else 0.0


@dataclass
class StagnationReport:
    """The verdict for the recent window of rounds."""

    stagnant: bool
    signal: float = 0.0  # mean correlation (vector mode) or 1.0/0.0 (signature mode)
    reason: str = ""
    persistent_failures: list[int] = field(default_factory=list)  # item indices, vector mode


class StagnationDetector:
    """Flags when recent improvement rounds keep making the same mistakes."""

    def __init__(self, *, window: int = 3, corr_threshold: float = 0.9, min_items: int = 4) -> None:
        self.window = max(2, window)
        self.corr_threshold = corr_threshold
        self.min_items = min_items
        self._vectors: list[list[float]] = []
        self._signatures: list[str] = []

    def record_vector(self, outcomes: Sequence[float]) -> None:
        """Record one round's per-item error vector (higher = worse; e.g. 1.0 = failed)."""
        self._vectors.append([float(x) for x in outcomes])

    def record_signature(self, signature: str) -> None:
        """Record one round's failure signature (fault hint / verifier output)."""
        self._signatures.append(_normalize_signature(signature))

    def assess(self) -> StagnationReport:
        """Judge the last ``window`` rounds. Vector mode wins when vectors were recorded."""
        if self._vectors:
            return self._assess_vectors()
        if self._signatures:
            return self._assess_signatures()
        return StagnationReport(False)

    def _assess_vectors(self) -> StagnationReport:
        recent = self._vectors[-self.window :]
        if len(recent) < self.window:
            return StagnationReport(False, reason="not enough rounds yet")
        width = len(recent[0])
        if width < self.min_items or any(len(v) != width for v in recent):
            return StagnationReport(False, reason="vectors too short or ragged")
        corr = mean_pairwise_correlation(recent)
        persistent = [i for i in range(width) if all(v[i] > _FAIL_LEVEL for v in recent)]
        stagnant = corr >= self.corr_threshold and bool(persistent)
        reason = (
            f"last {self.window} rounds correlate at {corr:.2f} (>= {self.corr_threshold}); "
            f"{len(persistent)} item(s) failed every round"
            if stagnant
            else f"correlation {corr:.2f} below {self.corr_threshold} or no persistent failures"
        )
        return StagnationReport(stagnant, round(corr, 3), reason, persistent)

    def _assess_signatures(self) -> StagnationReport:
        recent = self._signatures[-self.window :]
        if len(recent) < self.window:
            return StagnationReport(False, reason="not enough rounds yet")
        stagnant = all(sig and sig == recent[0] for sig in recent)
        reason = (
            f"last {self.window} attempts failed with the same signature"
            if stagnant
            else "recent failure signatures differ"
        )
        return StagnationReport(stagnant, 1.0 if stagnant else 0.0, reason)

    def advice(self) -> str:
        """A pivot instruction to fold into the next round's context."""
        report = self.assess()
        base = (
            "Stagnation detected: the last few attempts keep failing the same way. "
            "Do NOT refine the current approach — pivot to a fundamentally different "
            "strategy (a different method, decomposition, or tool), even if it feels riskier."
        )
        if report.persistent_failures:
            base += f" Persistently failing item indices: {report.persistent_failures}."
        return base
