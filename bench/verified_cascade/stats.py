"""The registered statistics of the verified-cascade bench (PREREGISTRATION.md §7). Stdlib only.

Exact two-sided McNemar, Holm, Wilson, Newcombe's method 10 for a paired difference of proportions,
a question-clustered bootstrap, a paired bootstrap of a cost ratio, AUROC (Mann-Whitney, ties at
one half), Brier and a 10-bin ECE.
"""

from __future__ import annotations

import math
import random
from collections.abc import Hashable, Sequence
from math import comb


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar on the discordant pairs b and c."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, float(2 * sum(comb(n, i) for i in range(k + 1))) / float(2**n))


def holm(pvalues: dict[str, float]) -> dict[str, float]:
    """Holm-adjusted p-values (step-down, monotone, capped at 1)."""
    order = sorted(pvalues, key=lambda k: pvalues[k])
    m = len(order)
    out: dict[str, float] = {}
    running = 0.0
    for i, key in enumerate(order):
        running = max(running, min(1.0, (m - i) * pvalues[key]))
        out[key] = running
    return out


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def newcombe_paired(x: Sequence[bool], y: Sequence[bool], z: float = 1.959964) -> tuple[float, float, float]:
    """Difference p(x) - p(y) of two paired binary outcomes, with Newcombe's (1998) method 10 95% CI."""
    if len(x) != len(y):
        raise ValueError("paired sequences differ in length")
    n = len(x)
    if n == 0:
        return (0.0, -1.0, 1.0)
    e = sum(1 for a, b in zip(x, y, strict=True) if a and b)
    f = sum(1 for a, b in zip(x, y, strict=True) if a and not b)
    g = sum(1 for a, b in zip(x, y, strict=True) if not a and b)
    h = n - e - f - g
    p1, p2 = (e + f) / n, (e + g) / n
    l1, u1 = wilson(e + f, n, z)
    l2, u2 = wilson(e + g, n, z)
    den = (e + f) * (g + h) * (e + g) * (f + h)
    # Newcombe's corrected phi: A = eh - fg is shrunk by n/2 toward 0 when positive (and floored at 0),
    # left as is when negative. Reproduces his worked example (12, 9, 2, 21): 0.0112 to 0.2954.
    a = e * h - f * g
    a_star = a - n / 2 if a > n / 2 else (0.0 if a >= 0 else float(a))
    phi = a_star / math.sqrt(den) if den > 0 else 0.0
    d = p1 - p2
    delta = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    eps = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    return (d, max(-1.0, d - delta), min(1.0, d + eps))


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def clustered_bootstrap_diff(
    x: Sequence[float], y: Sequence[float], clusters: Sequence[Hashable], *, reps: int = 10_000, seed: int = 26
) -> tuple[float, float, float]:
    """Mean of x - y, and a 95% percentile interval resampling whole clusters (questions)."""
    if not x:
        return (0.0, float("nan"), float("nan"))
    by: dict[Hashable, list[float]] = {}
    for a, b, c in zip(x, y, clusters, strict=True):
        by.setdefault(c, []).append(a - b)
    keys = sorted(by, key=str)
    sums = [sum(by[k]) for k in keys]
    counts = [len(by[k]) for k in keys]
    rng = random.Random(seed)
    stats: list[float] = []
    m = len(keys)
    for _ in range(reps):
        tot = cnt = 0.0
        for _j in range(m):
            i = rng.randrange(m)
            tot += sums[i]
            cnt += counts[i]
        stats.append(tot / cnt)
    point = sum(sums) / sum(counts)
    return (point, percentile(stats, 0.025), percentile(stats, 0.975))


def bootstrap_ratio(num: Sequence[float], den: Sequence[float], *, reps: int = 10_000, seed: int = 26) -> tuple[float, float, float]:
    """Paired ratio of means sum(num)/sum(den), with a 95% interval resampling items."""
    n = len(num)
    if n == 0 or sum(den) <= 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        d = sum(den[i] for i in idx)
        if d > 0:
            stats.append(sum(num[i] for i in idx) / d)
    return (sum(num) / sum(den), percentile(stats, 0.025), percentile(stats, 0.975))


def auroc(scores_pos: Sequence[float], scores_neg: Sequence[float]) -> float:
    """P(score of a positive > score of a negative), ties counted one half."""
    if not scores_pos or not scores_neg:
        return float("nan")
    wins = 0.0
    for p in scores_pos:
        for q in scores_neg:
            wins += 1.0 if p > q else 0.5 if p == q else 0.0
    return wins / (len(scores_pos) * len(scores_neg))


def brier(probs: Sequence[float], outcomes: Sequence[bool]) -> float:
    if not probs:
        return float("nan")
    return sum((p - (1.0 if o else 0.0)) ** 2 for p, o in zip(probs, outcomes, strict=True)) / len(probs)


def ece(probs: Sequence[float], outcomes: Sequence[bool], bins: int = 10) -> float:
    """Expected calibration error over equal-width bins, weighted by bin size."""
    if not probs:
        return float("nan")
    total = 0.0
    n = len(probs)
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, o) for p, o in zip(probs, outcomes, strict=True) if (lo <= p < hi) or (b == bins - 1 and p == 1.0)]
        if sel:
            conf = sum(p for p, _ in sel) / len(sel)
            acc = sum(1 for _, o in sel if o) / len(sel)
            total += len(sel) / n * abs(conf - acc)
    return total


def ece_floor(probs: Sequence[float], bins: int = 10, *, reps: int = 200, seed: int = 26) -> float:
    """The ECE a perfectly calibrated reader would show at this n: outcomes drawn from the probs."""
    if not probs:
        return float("nan")
    rng = random.Random(seed)
    vals = [ece(probs, [rng.random() < p for p in probs], bins) for _ in range(reps)]
    return sum(vals) / len(vals)


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Cohen's kappa of two raters over the same items."""
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    cats = set(a) | set(b)
    pe = sum((sum(1 for x in a if x == c) / n) * (sum(1 for y in b if y == c) / n) for c in cats)
    return 1.0 if pe >= 1.0 else (po - pe) / (1 - pe)
