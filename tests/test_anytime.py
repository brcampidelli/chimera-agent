"""Tests for the small-sample confidence bounds (SEA)."""

from __future__ import annotations

from chimera.eval.anytime import proportion_diff_ci, wilson_bounds, wilson_lower


def test_wilson_bounds_are_ordered_and_in_range() -> None:
    lo, hi = wilson_bounds(50, 100)
    assert 0.0 <= lo < 0.5 < hi <= 1.0
    assert abs((lo + hi) / 2 - 0.5) < 0.01  # centered near 0.5 for 50/100


def test_wilson_extremes_stay_in_unit_interval() -> None:
    assert wilson_bounds(0, 10)[0] == 0.0  # no false-negative below 0
    assert 0.99 < wilson_bounds(10, 10)[1] <= 1.0  # near 1, never overshooting
    assert wilson_bounds(0, 0) == (0.0, 1.0)  # no data -> no information


def test_wilson_lower_rises_with_successes() -> None:
    assert wilson_lower(1, 3) < wilson_lower(2, 3) < wilson_lower(3, 3)


def test_wilson_lower_flags_small_sample_luck() -> None:
    # 2/3 looks like 0.67 but the honest lower bound is well under 0.5.
    assert wilson_lower(2, 3) < 0.5
    # A perfect 3-model panel is still wide; large samples tighten toward the point.
    assert wilson_lower(3, 3) < wilson_lower(30, 30)


def test_proportion_diff_ci_detects_real_gap() -> None:
    lo, hi = proportion_diff_ci(9, 10, 1, 10)  # 90% vs 10%
    assert lo > 0.0 and hi <= 1.0  # significantly higher


def test_proportion_diff_ci_contains_zero_when_equal() -> None:
    lo, hi = proportion_diff_ci(5, 10, 5, 10)
    assert lo < 0.0 < hi  # no significant difference


def test_proportion_diff_ci_empty_sample() -> None:
    assert proportion_diff_ci(0, 0, 3, 5) == (-1.0, 1.0)
