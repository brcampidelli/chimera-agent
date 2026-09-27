"""The statistics `bench/verified_cascade/report.py` reads its verdict from (PREREGISTRATION.md §7).

Pinned against numbers that do not come from this code: the exact McNemar against the binomial tail
the pre-registration quotes (§6: p = 0.0625 at b = 5, 0.031 at b = 6 with c = 0), Newcombe's method 10
against his own worked example (12, 9, 2, 21 → 0.0112 to 0.2954), and the rest against cases whose
answer is known by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.verified_cascade import stats  # noqa: E402


def test_exact_mcnemar_is_the_binomial_tail_the_preregistration_quotes() -> None:
    assert stats.mcnemar_exact(5, 0) == pytest.approx(0.0625)
    assert stats.mcnemar_exact(6, 0) == pytest.approx(0.03125)
    assert stats.mcnemar_exact(10, 3) == pytest.approx(0.09228515625)
    assert stats.mcnemar_exact(0, 0) == 1.0
    assert stats.mcnemar_exact(3, 7) == stats.mcnemar_exact(7, 3)


def test_holm_is_step_down_and_monotone() -> None:
    adj = stats.holm({"a": 0.01, "b": 0.04, "c": 0.03})
    assert adj["a"] == pytest.approx(0.03)
    assert adj["c"] == pytest.approx(0.06)
    assert adj["b"] == pytest.approx(0.06)  # 1 x 0.04 would be smaller: monotonicity carries 0.06 over
    assert stats.holm({"x": 0.7, "y": 0.8}) == {"x": 1.0, "y": 1.0}


def test_newcombe_method_10_reproduces_his_worked_example() -> None:
    x = [True] * 21 + [False] * 23  # e + f = 21 of 44
    y = [True] * 12 + [False] * 9 + [True] * 2 + [False] * 21  # e = 12, f = 9, g = 2, h = 21
    diff, lo, hi = stats.newcombe_paired(x, y)
    assert diff == pytest.approx(7 / 44)
    assert lo == pytest.approx(0.0112, abs=5e-4)
    assert hi == pytest.approx(0.2954, abs=5e-4)


def test_newcombe_on_identical_outcomes_is_a_zero_difference_that_straddles_zero() -> None:
    x = [True] * 5 + [False] * 95
    diff, lo, hi = stats.newcombe_paired(x, list(x))
    assert diff == 0.0
    assert lo < 0.0 < hi


def test_a_cluster_bootstrap_over_singletons_centres_on_the_paired_mean() -> None:
    x = [1.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    y = [0.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    point, lo, hi = stats.clustered_bootstrap_diff(x, y, list(range(6)), reps=500)
    assert point == pytest.approx(1 / 6)
    assert lo <= point <= hi
    # Same seed, same interval: the registered seed makes the interval reproducible.
    assert stats.clustered_bootstrap_diff(x, y, list(range(6)), reps=500) == (point, lo, hi)


def test_a_cluster_moves_as_one_unit() -> None:
    """Two items of one question are resampled together, so a question whose items disagree in the
    same direction widens the interval more than two independent items would."""
    x = [1.0] * 10 + [0.0] * 10
    y = [0.0] * 20
    pairs = [f"q{i // 2}" for i in range(20)]  # each question's two items move together
    singles = [f"i{i}" for i in range(20)]
    _, lo_c, hi_c = stats.clustered_bootstrap_diff(x, y, pairs, reps=4000)
    _, lo_i, hi_i = stats.clustered_bootstrap_diff(x, y, singles, reps=4000)
    assert hi_c - lo_c > hi_i - lo_i


def test_auroc_brier_ece_and_kappa_on_known_cases() -> None:
    assert stats.auroc([0.9, 0.8], [0.1, 0.2]) == 1.0
    assert stats.auroc([0.5], [0.5]) == 0.5
    assert stats.brier([1.0, 0.0], [True, False]) == 0.0
    assert stats.ece([0.0, 1.0], [False, True]) == 0.0
    assert stats.ece([0.95] * 10, [False] * 10) == pytest.approx(0.95)
    assert stats.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0
    assert stats.wilson(0, 10)[0] == 0.0


def test_the_cost_ratio_is_a_ratio_of_means_with_an_interval_around_it() -> None:
    ratio, lo, hi = stats.bootstrap_ratio([2.0, 4.0, 6.0], [1.0, 2.0, 3.0], reps=300)
    assert ratio == pytest.approx(2.0)
    assert lo == pytest.approx(2.0) and hi == pytest.approx(2.0)
