"""Small-sample confidence bounds for Bernoulli metrics (SEA, arXiv:2607.00871).

Chimera grades pass/fail (Bernoulli) outcomes — skill transfer, per-task success,
degradation. A bare point estimate over a handful of trials is noise reported as signal.
This module gives the cheap, honest, fixed-sample tools: a Wilson score interval on a
single proportion and a Newcombe confidence interval on the difference of two proportions.

Deliberately NOT the paper's anytime-valid confidence sequences / e-processes / harmonic
error budget: those are for a continuous-peeking accept loop Chimera does not run, they
multiply model calls, and the paper's own authors dropped that gate for cost. Pure Python,
stdlib ``math`` only.
"""

from __future__ import annotations

import math
from statistics import NormalDist

Z95 = 1.959963984540054  # standard normal quantile for a 95% two-sided interval


def wilson_bounds(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, clamped to [0, 1].

    Returns ``(0.0, 1.0)`` for ``n == 0`` (no information). Unlike the normal
    approximation, this stays inside [0, 1] and is sane at 0/n and n/n.
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def wilson_lower(successes: int, n: int, z: float = Z95) -> float:
    """Lower Wilson bound — the honest 'at least this good' estimate of a pass rate."""
    return wilson_bounds(successes, n, z)[0]


def z_for(alpha: float) -> float:
    """Two-sided normal quantile for confidence ``1 - alpha`` (``alpha=0.05`` gives ``Z95``)."""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    return NormalDist().inv_cdf(1.0 - alpha / 2.0)


def wilson_lower_best_of(successes: int, n: int, k: int, alpha: float = 0.05) -> float:
    """Wilson lower bound for a candidate that was picked as the **best of k**.

    Scoring k candidates and keeping the top one is not the same as scoring one: the maximum of k
    noisy estimates is biased upward (the winner's curse), so a plain bound taken on the winner
    overstates it, and the overstatement grows with k. Measured on Chimera's own default gate —
    3 candidates, 3 trials each, threshold 0.5 — best-of-3 accepted a candidate whose true pass
    rate was 0.3 **51.8%** of the time, against 21.6% for a single candidate.

    The correction is a Bonferroni split of the error budget: spend ``alpha / k`` instead of
    ``alpha``, which tightens the bound as k grows. This costs **no extra model calls** — it only
    moves the quantile — which is what makes it worth doing here, unlike the anytime-valid
    machinery this module deliberately skips (see the module docstring).

    ``k <= 1`` returns the ordinary bound.
    """
    if k <= 1:
        return wilson_lower(successes, n, z_for(alpha))
    return wilson_lower(successes, n, z_for(alpha / k))


def best_possible_wilson(n: int, k: int = 1, alpha: float = 0.05) -> float:
    """The bound a **perfect** ``n/n`` run would earn — the ceiling of a Wilson-mode gate.

    Use it to catch an unsatisfiable configuration before it silently rejects everything: at n=3 a
    flawless 3/3 scores only 0.439, so a 0.5 threshold can never be met by any result at all.
    """
    return wilson_lower_best_of(n, n, k, alpha)


def proportion_diff_ci(
    s1: int, n1: int, s2: int, n2: int, z: float = Z95
) -> tuple[float, float]:
    """Newcombe confidence interval for ``p1 - p2`` (two independent proportions).

    Built from the two Wilson intervals, so it inherits their good small-sample
    behaviour. Returns ``(-1.0, 1.0)`` if either sample is empty. A lower bound > 0
    means p1 is significantly greater than p2 at the given confidence.
    """
    if n1 <= 0 or n2 <= 0:
        return (-1.0, 1.0)
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = wilson_bounds(s1, n1, z)
    l2, u2 = wilson_bounds(s2, n2, z)
    diff = p1 - p2
    lower = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    upper = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (max(-1.0, lower), min(1.0, upper))
