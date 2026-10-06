"""The one home for the interval and test arithmetic behind every published number.

Before this module, the Wilson interval was written out in at least fifteen bench scripts and the
exact McNemar test in at least fifteen more, with three different values of ``z`` and two sign
conventions for the paired difference (study 30, S30-34). A copy that drifts does not fail: it
prints a slightly different interval under the same name. So the arithmetic lives here, inside the
mutation gate (`MUTATION.md`), and `tests/test_stats_helpers_have_one_home.py` refuses a new copy.

What is here, and the one rule for choosing between them (`bench/PROTOCOL.md` §11):

- one proportion -> :func:`wilson`;
- two independent proportions -> :func:`newcombe_unpaired` (Newcombe 1998, method 10 for the
  unpaired case: built from two Wilson intervals);
- two proportions on the SAME items (paired binary) -> :func:`bonett_price_paired` for the interval
  and :func:`mcnemar_exact` for the p-value. :func:`newcombe_paired` is kept because several
  published readers used it; it covers about as well;
- a mean of per-item differences (continuous, e.g. per-task means) -> :func:`mean_t_interval`;
  two independent groups of such values (an interaction between strata) -> :func:`welch_t_interval`;
- a median of small integers -> :func:`median_interval` (binomial order statistics);
- an AUROC re-read from its own summary -> :func:`auroc_hanley_mcneil`;
- "no worse than / the same as, within a margin declared before the run" -> the TOST helpers
  (:func:`tost_paired`, :func:`tost_unpaired`, :func:`tost_mean`), which read a ``1 - 2α``
  interval against the margin (Schuirmann 1987).

Deliberately absent: the percentile bootstrap. arXiv 2609.35815 (evalstats) measures it covering
88% at a nominal 95% with N<100, and no variant reaching nominal on paired binary data even at
N=100. Every function below is closed-form and stdlib-only, so a reader can recompute a published
interval by hand.

Every paired function takes the discordant counts by ROLE — ``baseline_only`` (the baseline passed,
the treatment failed) and ``treatment_only`` — and returns intervals for ``treatment − baseline``.
The bench copies disagreed on the sign; naming the roles is what makes the sign unambiguous.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean, stdev

Z95 = 1.959963984540054  # standard normal quantile for a 95% two-sided interval


def z_two_sided(alpha: float) -> float:
    """Normal quantile for a two-sided ``1 - alpha`` interval (``alpha=0.05`` gives :data:`Z95`)."""
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    return NormalDist().inv_cdf(1.0 - alpha / 2.0)


# --------------------------------------------------------------------------------------------------
# One proportion, two independent proportions


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion, clamped to [0, 1].

    ``(0.0, 1.0)`` for ``n <= 0``: no trials is no information, never certainty at 0 (three of the
    bench copies returned ``(0.0, 0.0)`` there).
    """
    if n <= 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def newcombe_unpaired(s1: int, n1: int, s2: int, n2: int, z: float = Z95) -> tuple[float, float]:
    """Newcombe's hybrid score interval for ``p1 - p2``, two INDEPENDENT proportions.

    ``(-1.0, 1.0)`` if either sample is empty.
    """
    if n1 <= 0 or n2 <= 0:
        return (-1.0, 1.0)
    p1, p2 = s1 / n1, s2 / n2
    l1, u1 = wilson(s1, n1, z)
    l2, u2 = wilson(s2, n2, z)
    diff = p1 - p2
    lower = diff - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    upper = diff + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return (max(-1.0, lower), min(1.0, upper))


# --------------------------------------------------------------------------------------------------
# Paired binary


def mcnemar_exact(baseline_only: int, treatment_only: int) -> float:
    """Two-sided exact McNemar p-value: a binomial(m, 1/2) test on the ``m`` discordant pairs.

    1.0 when there are no discordant pairs. Symmetric in its two arguments.
    """
    m = baseline_only + treatment_only
    if m == 0:
        return 1.0
    k = min(baseline_only, treatment_only)
    tail = sum(math.comb(m, i) for i in range(k + 1)) / (1 << m)
    return min(1.0, 2.0 * tail)


def bonett_price_paired(
    baseline_only: int, treatment_only: int, n: int, z: float = Z95
) -> tuple[float, float]:
    """Bonett-Price adjusted Wald interval for ``treatment − baseline`` on paired binary data.

    Bonett & Price (2012), *J. Educ. Behav. Stat.* 37(4): add one to each discordant cell and two to
    ``n``, then a Wald interval. It is the paired-binary interval arXiv 2609.35815 recommends and
    Fagerland et al. (2014) rank first; it keeps the uncertainty in HOW MANY pairs disagree, which
    the conditional interval :mod:`chimera.eval.paired` used before (Wilson on the discordant pairs,
    scaled by the observed ``m/n``) treated as known — measured here at 41-88% coverage of a real
    difference (`tests/test_the_paired_interval_covers_the_difference.py`).

    Clamped to [-1, 1]; ``(-1.0, 1.0)`` for ``n <= 0``.
    """
    if n <= 0:
        return (-1.0, 1.0)
    p_treat = (treatment_only + 1) / (n + 2)
    p_base = (baseline_only + 1) / (n + 2)
    diff = p_treat - p_base
    # max(0, ...) only guards rounding: p_treat + p_base >= diff**2 holds for any counts.
    se = math.sqrt(max(0.0, (p_treat + p_base - diff * diff) / (n + 2)))
    return (max(-1.0, diff - z * se), min(1.0, diff + z * se))


def newcombe_paired(
    both_pass: int,
    baseline_only: int,
    treatment_only: int,
    both_fail: int,
    z: float = Z95,
    *,
    phi_correction: bool = True,
) -> tuple[float, float]:
    """Newcombe (1998) method 10 interval for ``treatment − baseline`` on paired binary data.

    Two Wilson intervals on the marginals, combined with the continuity-corrected phi correlation
    between the arms. ``(-1.0, 1.0)`` for no pairs.

    ``phi_correction=False`` is NOT method 10: it is the uncorrected phi three bench readers used
    (`sharded_recap`, `spoken_standard`, `unattended_claims`), narrower than the paper's interval.
    It exists only so `bench/interval_reread` can reproduce what they published before re-reading it.
    """
    n = both_pass + baseline_only + treatment_only + both_fail
    if n <= 0:
        return (-1.0, 1.0)
    treat_pass = both_pass + treatment_only
    base_pass = both_pass + baseline_only
    p_t, p_b = treat_pass / n, base_pass / n
    lt, ut = wilson(treat_pass, n, z)
    lb, ub = wilson(base_pass, n, z)
    product = treat_pass * (n - treat_pass) * base_pass * (n - base_pass)
    if product == 0:
        phi = 0.0
    else:
        cross = both_pass * both_fail - baseline_only * treatment_only
        if not phi_correction:
            corrected = float(cross)
        elif cross > n / 2:
            corrected = cross - n / 2
        elif cross >= 0:
            corrected = 0.0
        else:
            corrected = float(cross)
        phi = corrected / math.sqrt(product)
    diff = p_t - p_b
    low_span = (p_t - lt) ** 2 - 2 * phi * (p_t - lt) * (ub - p_b) + (ub - p_b) ** 2
    high_span = (ut - p_t) ** 2 - 2 * phi * (ut - p_t) * (p_b - lb) + (p_b - lb) ** 2
    lower = diff - math.sqrt(max(0.0, low_span))
    upper = diff + math.sqrt(max(0.0, high_span))
    return (max(-1.0, lower), min(1.0, upper))


def mover_difference(
    first: float, first_ci: tuple[float, float], second: float, second_ci: tuple[float, float]
) -> tuple[float, float, float]:
    """``(diff, low, high)`` for ``first − second`` from two INDEPENDENT estimates and their intervals.

    Zou & Donner's MOVER (method of variance estimates recovery): each side of the difference takes
    the distance from each estimate to the relevant end of its own interval, so asymmetric intervals
    (Wilson, Bonett-Price) stay asymmetric. Used for Youden's J = TPR − FPR read as ΔTPR − ΔFPR,
    where the two come from disjoint item sets. Newcombe's unpaired interval is this with two Wilsons.
    """
    diff = first - second
    low = diff - math.sqrt((first - first_ci[0]) ** 2 + (second_ci[1] - second) ** 2)
    high = diff + math.sqrt((first_ci[1] - first) ** 2 + (second - second_ci[0]) ** 2)
    return (diff, low, high)


def conditional_wilson_paired(
    baseline_only: int, treatment_only: int, n: int, z: float = Z95
) -> tuple[float, float]:
    """SUPERSEDED: the interval :mod:`chimera.eval.paired` printed until study 30. Never read it anew.

    A Wilson interval on the share ``q`` of the ``m`` discordant pairs the treatment won, mapped to
    the difference by ``(m/n)·(2q − 1)`` with the OBSERVED ``m/n`` treated as known. That loses the
    uncertainty in ``m`` and covers a real difference only 41-88% of the time at a nominal 95%; it
    returns ``(0.0, 0.0)`` when no pair disagrees. It is kept, named for what it is, only so the
    readers whose pre-registrations fixed it (`bench/review_judge`) still reproduce the numbers they
    published — the published numbers are re-read with :func:`bonett_price_paired` in
    `bench/interval_reread`.
    """
    m = baseline_only + treatment_only
    if n <= 0:
        return (-1.0, 1.0)
    if m == 0:
        return (0.0, 0.0)
    low, high = wilson(treatment_only, m, z)
    scale = m / n
    return (scale * (2 * low - 1), scale * (2 * high - 1))


# --------------------------------------------------------------------------------------------------
# A mean of differences (continuous)


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the regularized incomplete beta (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        step = d * c
        h *= step
        if abs(step - 1.0) < 1e-15:
            break
    return h


def _incomplete_beta(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) for x in [0, 1]."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    log_front = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log1p(-x)
    front = math.exp(log_front)
    # The continued fraction converges for x < (a+1)/(a+b+2); use the symmetry otherwise.
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, df: float) -> float:
    """Student-t cumulative distribution, ``P(T <= t)`` with ``df`` degrees of freedom."""
    if df <= 0:
        raise ValueError(f"df must be positive, got {df}")
    tail = 0.5 * _incomplete_beta(df / 2.0, 0.5, df / (df + t * t))
    return 1.0 - tail if t > 0 else tail


def t_quantile(p: float, df: float) -> float:
    """Inverse of :func:`t_cdf` by bisection, to 1e-10. ``p`` in (0, 1)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    if p < 0.5:
        return -t_quantile(1.0 - p, df)
    low, high = 0.0, 1.0
    while t_cdf(high, df) < p:  # widen until the quantile is bracketed (df=1 needs ~64 at 0.995)
        high *= 2.0
    for _ in range(200):
        mid = (low + high) / 2.0
        if t_cdf(mid, df) < p:
            low = mid
        else:
            high = mid
        if high - low < 1e-10:
            break
    return (low + high) / 2.0


def mean_t_interval(values: Sequence[float], alpha: float = 0.05) -> tuple[float, float, float]:
    """``(mean, low, high)``: the one-sample t interval on a mean of per-item differences.

    The replacement for a percentile bootstrap over a handful of tasks: a paired design reduces to
    one difference per task, and over 7-32 tasks the t interval is the closed-form reading whose
    coverage does not depend on how many tasks there were. Fewer than two values cannot carry a
    spread, so the interval is infinite rather than invented — and the same holds for values that
    show no spread at all: three tasks with the same share say nothing about how much a fourth could
    differ, and a zero-width interval would be certainty read off a handful of items. A caller that
    meets ``(-inf, inf)`` reports "no spread observed, interval undefined", not a number.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not values:
        return (math.nan, -math.inf, math.inf)
    mean = fmean(values)
    if len(values) < 2:
        return (mean, -math.inf, math.inf)
    spread = stdev(values)
    if spread == 0.0:
        return (mean, -math.inf, math.inf)
    half = t_quantile(1.0 - alpha / 2.0, len(values) - 1) * spread / math.sqrt(len(values))
    return (mean, mean - half, mean + half)


def welch_t_interval(
    first: Sequence[float], second: Sequence[float], alpha: float = 0.05
) -> tuple[float, float, float]:
    """``(diff, low, high)`` for ``mean(first) − mean(second)``, two independent groups (Welch).

    Unequal variances, Welch-Satterthwaite degrees of freedom. The closed-form reading of an
    interaction between two strata of tasks, where a bootstrap would resample 7 and 8 values.
    Either group under two values leaves no spread to estimate, and the interval is infinite; so
    does two groups with no spread at all (zero width would be invented certainty, as in
    :func:`mean_t_interval`).
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    if not first or not second:
        return (math.nan, -math.inf, math.inf)
    diff = fmean(first) - fmean(second)
    if len(first) < 2 or len(second) < 2:
        return (diff, -math.inf, math.inf)
    v1 = stdev(first) ** 2 / len(first)
    v2 = stdev(second) ** 2 / len(second)
    se = math.sqrt(v1 + v2)
    if se == 0.0:
        return (diff, -math.inf, math.inf)
    df = (v1 + v2) ** 2 / (v1 * v1 / (len(first) - 1) + v2 * v2 / (len(second) - 1))
    half = t_quantile(1.0 - alpha / 2.0, df) * se
    return (diff, diff - half, diff + half)


def median_interval(values: Sequence[float], alpha: float = 0.05) -> tuple[float, float, float]:
    """``(median, low, high)``: the distribution-free order-statistic interval for a median.

    The ``k``-th smallest and ``k``-th largest values, with ``k`` the largest count such that
    ``P(Binomial(n, 1/2) < k) <= alpha / 2`` — exact coverage of at least ``1 - alpha`` for any
    continuous distribution, and the closed-form replacement for a bootstrap of a median over a
    few dozen small integers. Below six values no pair of order statistics reaches 95%, and the
    interval is infinite rather than overstated.
    """
    if not 0.0 < alpha < 1.0:
        raise ValueError(f"alpha must be in (0, 1), got {alpha}")
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return (math.nan, -math.inf, math.inf)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    k = 0
    tail = 0.0
    while k < n:
        nxt = tail + math.comb(n, k) / (1 << n)
        if nxt > alpha / 2.0:
            break
        tail = nxt
        k += 1
    if k == 0:
        return (median, -math.inf, math.inf)
    return (median, ordered[k - 1], ordered[n - k])


def auroc_hanley_mcneil(
    auc: float, n_positive: int, n_negative: int, z: float = Z95
) -> tuple[float, float]:
    """Hanley & McNeil (1982) interval for an AUROC, from the AUC and the two class sizes.

    Closed form, so a published AUROC can be re-read from its own summary. ``(0.0, 1.0)`` when
    either class is empty. Clamped to [0, 1].
    """
    if n_positive <= 0 or n_negative <= 0:
        return (0.0, 1.0)
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    variance = (
        auc * (1.0 - auc) + (n_positive - 1) * (q1 - auc * auc) + (n_negative - 1) * (q2 - auc * auc)
    ) / (n_positive * n_negative)
    se = math.sqrt(max(0.0, variance))
    return (max(0.0, auc - z * se), min(1.0, auc + z * se))


# --------------------------------------------------------------------------------------------------
# Equivalence and non-inferiority (TOST)


@dataclass(frozen=True)
class Equivalence:
    """A TOST reading: the ``1 - 2α`` interval and whether it sits inside ``(-margin, +margin)``.

    ``non_inferior`` reads only the lower side (``low > -margin``) — the one-sided half of the same
    test — so a pre-registration that declared non-inferiority reads that field, and one that
    declared equivalence reads ``equivalent``. Neither is a verdict about a difference: "not
    significant" is not "equivalent" (arXiv 2610.00047), which is why this exists.
    """

    low: float
    high: float
    margin: float
    alpha: float

    @property
    def equivalent(self) -> bool:
        return -self.margin < self.low and self.high < self.margin

    @property
    def non_inferior(self) -> bool:
        return self.low > -self.margin


def _check_margin(margin: float, alpha: float) -> None:
    if not margin > 0.0:
        raise ValueError(f"the equivalence margin must be positive, got {margin}")
    if not 0.0 < alpha < 0.5:
        raise ValueError(f"alpha must be in (0, 0.5) for TOST, got {alpha}")


def tost_paired(
    baseline_only: int, treatment_only: int, n: int, margin: float, alpha: float = 0.05
) -> Equivalence:
    """TOST on paired binary data, on the Bonett-Price interval at ``1 - 2α``."""
    _check_margin(margin, alpha)
    low, high = bonett_price_paired(baseline_only, treatment_only, n, z_two_sided(2 * alpha))
    return Equivalence(low, high, margin, alpha)


def tost_unpaired(
    s_treatment: int, n_treatment: int, s_baseline: int, n_baseline: int, margin: float, alpha: float = 0.05
) -> Equivalence:
    """TOST on two independent proportions (``treatment − baseline``), Newcombe at ``1 - 2α``."""
    _check_margin(margin, alpha)
    low, high = newcombe_unpaired(s_treatment, n_treatment, s_baseline, n_baseline, z_two_sided(2 * alpha))
    return Equivalence(low, high, margin, alpha)


def tost_mean(values: Sequence[float], margin: float, alpha: float = 0.05) -> Equivalence:
    """TOST on a mean of per-item differences, the t interval at ``1 - 2α``."""
    _check_margin(margin, alpha)
    _, low, high = mean_t_interval(values, 2 * alpha)
    return Equivalence(low, high, margin, alpha)
