"""The paired interval must cover the difference it reports — the property it existed for.

`PairedResult.diff_ci` used to be a Wilson interval on the share of discordant pairs the treatment
won, scaled by the OBSERVED ``m/n``. That scale is itself an estimate, and treating it as known
throws its uncertainty away: in a seeded simulation the interval covered the true difference
41-88% of the time whenever there was a real difference to cover, and at 95% nominal. It also
returned a zero-width ``(0.0, 0.0)`` whenever the arms happened to agree on every pair — certainty
from four items — and called four discordant pairs to none "significant", where the exact McNemar
p-value is 0.125. The test of "is there any difference" was roughly calibrated; the interval it
printed, which RESULTS files quote and compare to margins, was not.
"""

from __future__ import annotations

import random

import pytest

from chimera.eval.paired import compare_paired, verdict_text
from chimera.eval.proportions import mcnemar_exact


def _table(rng: random.Random, n: int, p_both: float, p_base_only: float, p_treat_only: float) -> tuple[list[bool], list[bool]]:
    base: list[bool] = []
    treat: list[bool] = []
    for _ in range(n):
        u = rng.random()
        if u < p_both:
            base.append(True), treat.append(True)
        elif u < p_both + p_base_only:
            base.append(True), treat.append(False)
        elif u < p_both + p_base_only + p_treat_only:
            base.append(False), treat.append(True)
        else:
            base.append(False), treat.append(False)
    return base, treat


@pytest.mark.parametrize(
    ("n", "p_both", "p_base_only", "p_treat_only"),
    [
        (25, 0.6, 0.0, 0.2),  # a one-sided lift on a small bench: the old interval covered 58%
        (50, 0.5, 0.02, 0.3),  # a large lift: 65%
        (100, 0.3, 0.05, 0.2),  # a moderate lift at n=100: 86%
        (200, 0.3, 0.02, 0.4),  # more data does not fix it — the bias is in the method: 66%
    ],
)
def test_the_paired_interval_covers_a_real_difference_at_its_nominal_rate(
    n: int, p_both: float, p_base_only: float, p_treat_only: float
) -> None:
    rng = random.Random(n * 7 + 1)
    truth = p_treat_only - p_base_only
    draws = 800
    covered = 0
    for _ in range(draws):
        base, treat = _table(rng, n, p_both, p_base_only, p_treat_only)
        low, high = compare_paired(base, treat).diff_ci
        covered += low <= truth <= high
    assert covered / draws >= 0.93


def test_four_discordant_pairs_to_none_is_not_called_significant() -> None:
    # 8 both-pass, 8 both-fail, 4 treatment-only: the exact test says p = 0.125.
    base = [True] * 8 + [False] * 8 + [False] * 4
    treat = [True] * 8 + [False] * 8 + [True] * 4
    result = compare_paired(base, treat)
    assert mcnemar_exact(result.baseline_only, result.treatment_only) == 0.125
    assert result.significant is False
    low, high = result.diff_ci
    assert low < 0 < high


def test_agreement_on_four_pairs_is_not_a_zero_width_interval() -> None:
    same = [True, False, True, False]
    low, high = compare_paired(same, list(same)).diff_ci
    assert low < -0.1 and high > 0.1


def _counts(n: int, base_only: int, treat_only: int) -> tuple[list[bool], list[bool]]:
    rest = n - base_only - treat_only
    base = [True] * base_only + [False] * treat_only + [True] * (rest // 2) + [False] * (rest - rest // 2)
    treat = [False] * base_only + [True] * treat_only + [True] * (rest // 2) + [False] * (rest - rest // 2)
    return base, treat


@pytest.mark.parametrize(
    ("n", "base_only", "treat_only", "p"),
    [
        (4, 0, 4, 0.125),  # four to none, every pair discordant
        (6, 0, 4, 0.125),
        (20, 0, 5, 0.0625),  # five to none: blind_audit's `none` row is this table at n = 23
        (23, 0, 5, 0.0625),
        (20, 1, 7, 0.0703125),
        (50, 1, 7, 0.0703125),
    ],
)
def test_a_table_the_exact_test_does_not_reject_is_not_called_significant(
    n: int, base_only: int, treat_only: int, p: float
) -> None:
    # On these tables Bonett-Price alone prints an interval clear of zero. The exact test is what
    # PROTOCOL §11 names for the paired p, and it says no; the verdict that gates a flip says no too.
    result = compare_paired(*_counts(n, base_only, treat_only))
    assert (result.baseline_only, result.treatment_only, result.n) == (base_only, treat_only, n)
    assert mcnemar_exact(base_only, treat_only) == pytest.approx(p)
    low, high = result.diff_ci
    assert low > 0  # the interval alone would have said yes
    assert result.significant is False
    assert verdict_text(result).startswith("not significant (CI excludes 0, but exact McNemar p = ")


def test_a_table_both_checks_reject_is_still_significant() -> None:
    result = compare_paired(*_counts(20, 0, 6))  # p = 0.03125, Bonett-Price clear of zero
    assert result.significant is True
    assert verdict_text(result) == "significant (CI excludes 0)"


def test_significant_never_disagrees_with_either_check_on_any_small_table() -> None:
    for n in range(1, 41):
        for b in range(n + 1):
            for c in range(n - b + 1):
                result = compare_paired(*_counts(n, b, c))
                low, high = result.diff_ci
                both = (low > 0 or high < 0) and mcnemar_exact(b, c) <= 0.05
                assert result.significant is both, (n, b, c)
