"""The interval arithmetic every bench reads (`chimera/eval/proportions.py`), pinned by value.

Hand-computable values where they exist, published table values where they do not, and the
property the module exists for — coverage — checked by a seeded simulation small enough to run in
the mutation gate.
"""

from __future__ import annotations

import math
import random

import pytest

from chimera.eval.proportions import (
    Z95,
    Equivalence,
    auroc_hanley_mcneil,
    bonett_price_paired,
    conditional_wilson_paired,
    fisher_exact_greater,
    mcnemar_exact,
    mean_t_interval,
    median_interval,
    mover_difference,
    newcombe_paired,
    newcombe_unpaired,
    phi_coefficient,
    t_cdf,
    t_quantile,
    tost_mean,
    tost_paired,
    tost_unpaired,
    welch_t_interval,
    wilson,
    z_two_sided,
)

# --- one proportion -------------------------------------------------------------------------------


def test_wilson_matches_the_hand_computed_interval() -> None:
    low, high = wilson(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.277533, abs=1e-6)
    low, high = wilson(10, 10)
    assert low == pytest.approx(0.722467, abs=1e-6)
    assert high == pytest.approx(1.0, abs=1e-12)
    low, high = wilson(3, 10)
    assert (low, high) == pytest.approx((0.107791, 0.603222), abs=1e-6)


def test_wilson_at_zero_trials_is_no_information_not_certainty() -> None:
    assert wilson(0, 0) == (0.0, 1.0)
    assert wilson(0, -1) == (0.0, 1.0)


def test_wilson_narrows_with_a_smaller_z() -> None:
    wide = wilson(5, 20)
    narrow = wilson(5, 20, z=1.0)
    assert narrow[0] > wide[0] and narrow[1] < wide[1]


def test_z_two_sided_gives_the_familiar_quantiles_and_refuses_nonsense() -> None:
    assert z_two_sided(0.05) == pytest.approx(Z95, abs=1e-12)
    assert z_two_sided(0.10) == pytest.approx(1.644854, abs=1e-6)
    for bad in (0.0, 1.0, -0.1):
        with pytest.raises(ValueError):
            z_two_sided(bad)


# --- two independent proportions ------------------------------------------------------------------


def test_newcombe_unpaired_matches_the_hand_computed_interval() -> None:
    low, high = newcombe_unpaired(9, 10, 1, 10)
    assert (low, high) == pytest.approx((0.369867, 0.916141), abs=1e-6)
    low, high = newcombe_unpaired(5, 10, 5, 10)
    assert low == pytest.approx(-high, abs=1e-12) and low < 0 < high


def test_newcombe_unpaired_with_an_empty_arm_says_nothing() -> None:
    assert newcombe_unpaired(0, 0, 3, 5) == (-1.0, 1.0)
    assert newcombe_unpaired(3, 5, 0, 0) == (-1.0, 1.0)


# --- paired binary --------------------------------------------------------------------------------


def test_mcnemar_exact_is_the_two_sided_binomial_on_the_discordant_pairs() -> None:
    assert mcnemar_exact(0, 4) == 0.125  # 2 * (1/2)^4: four to nothing is NOT significant
    assert mcnemar_exact(4, 0) == 0.125
    assert mcnemar_exact(1, 9) == pytest.approx(0.021484375, abs=1e-12)
    assert mcnemar_exact(10, 5) == pytest.approx(0.301758, abs=1e-6)  # chat_history's published p
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0  # the doubled tail is clamped to a probability


def test_bonett_price_matches_its_formula() -> None:
    # n=20, 4 treatment-only, 0 baseline-only: (5/22 - 1/22) ± z * sqrt((6/22 - (4/22)^2) / 22)
    diff = 4 / 22
    se = math.sqrt((6 / 22 - diff**2) / 22)
    low, high = bonett_price_paired(0, 4, 20)
    assert low == pytest.approx(diff - Z95 * se, abs=1e-12)
    assert high == pytest.approx(diff + Z95 * se, abs=1e-12)
    assert low < 0 < high  # agrees with McNemar's p = 0.125 above


def test_bonett_price_is_signed_treatment_minus_baseline() -> None:
    low, high = bonett_price_paired(9, 1, 30)
    flipped = bonett_price_paired(1, 9, 30)
    assert (low, high) == pytest.approx((-flipped[1], -flipped[0]), abs=1e-12)
    assert high < 0


def test_bonett_price_with_no_disagreement_is_not_a_zero_width_interval() -> None:
    low, high = bonett_price_paired(0, 0, 10)
    assert low == pytest.approx(-high, abs=1e-12)
    assert high > 0.1


def test_bonett_price_is_clamped_and_empty_is_no_information() -> None:
    assert bonett_price_paired(0, 0, 0) == (-1.0, 1.0)
    low, high = bonett_price_paired(0, 2, 2)
    assert low >= -1.0 and high <= 1.0
    assert high == 1.0  # (3/4 - 1/4) + 1.96 * sqrt((1 - 1/4)/4) overshoots 1 and is clamped


def test_newcombe_paired_reproduces_the_interval_the_published_readers_printed() -> None:
    # bench/prompt_overlays/report.py printed this interval for (both, first-only 1, second-only 6,
    # neither) with first − second; here the roles are named, so it reads treatment − baseline.
    low, high = newcombe_paired(0, 6, 1, 0)
    assert (low, high) == pytest.approx((-0.948641, 0.026256), abs=1e-6)


def test_newcombe_paired_uses_the_continuity_corrected_phi() -> None:
    # A strongly concordant table (phi > 0) must be narrower than the same marginals with weaker
    # agreement. The value is cross-checked against an independent implementation of method 10.
    concordant = newcombe_paired(40, 2, 8, 50)
    low_c, high_c = concordant
    assert (low_c, high_c) == pytest.approx((-0.003521, 0.122183), abs=1e-6)
    discordant = newcombe_paired(20, 22, 28, 30)  # same marginals, weaker agreement
    assert high_c - low_c < discordant[1] - discordant[0]


def test_newcombe_paired_with_a_degenerate_margin_drops_phi_and_empty_says_nothing() -> None:
    # Every pair passed the baseline: one marginal is constant, so phi is undefined and taken as 0.
    low, high = newcombe_paired(5, 3, 0, 0)
    assert (low, high) == pytest.approx((-0.694258, 0.027441), abs=1e-6)
    assert newcombe_paired(0, 0, 0, 0) == (-1.0, 1.0)


def test_newcombe_paired_negative_cross_product_keeps_its_sign() -> None:
    # ad - bc < 0: phi is negative, which WIDENS the interval relative to phi = 0.
    low, high = newcombe_paired(1, 10, 10, 1)
    assert (low, high) == pytest.approx((-0.367615, 0.367615), abs=1e-6)


def test_newcombe_paired_reproduces_the_published_worked_examples() -> None:
    # Newcombe (1998), the worked example (12, 9, 2, 21): 0.0112 to 0.2954 for the first margin
    # minus the second — here the first arm is the treatment, so its 9 lone passes are treatment-only.
    assert newcombe_paired(12, 2, 9, 21) == pytest.approx((0.0112, 0.2954), abs=5e-5)
    # Fagerland, Lydersen & Laake (2014), Table V: counts 1, 1, 7, 12 give -0.507 to -0.026.
    assert newcombe_paired(1, 7, 1, 12) == pytest.approx((-0.507, -0.026), abs=5e-4)


def test_the_superseded_conditional_interval_still_reproduces_what_it_published() -> None:
    # bench/review_judge/results/full_read.txt, out of sample: ΔTPR +45.6 pp CI [+41.6, +45.6] with
    # 83 treatment-only and 0 baseline-only pairs out of 182.
    low, high = conditional_wilson_paired(0, 83, 182)
    assert (round(low * 100, 1), round(high * 100, 1)) == (41.6, 45.6)
    assert conditional_wilson_paired(0, 0, 10) == (0.0, 0.0)  # the zero-width interval it is retired for
    assert conditional_wilson_paired(0, 0, 0) == (-1.0, 1.0)
    # The same table under the method that replaced it is wider, and wider on the side it hid.
    bp_low, bp_high = bonett_price_paired(0, 83, 182)
    assert bp_low < low and bp_high > high


def _simulate(n: int, p_both: float, p_base_only: float, p_treat_only: float, draws: int, seed: int) -> list[tuple[int, int, int, int]]:
    rng = random.Random(seed)
    tables = []
    for _ in range(draws):
        a = b = c = d = 0
        for _ in range(n):
            u = rng.random()
            if u < p_both:
                a += 1
            elif u < p_both + p_base_only:
                b += 1
            elif u < p_both + p_base_only + p_treat_only:
                c += 1
            else:
                d += 1
        tables.append((a, b, c, d))
    return tables


@pytest.mark.parametrize(
    ("n", "p_both", "p_base_only", "p_treat_only"),
    [(25, 0.6, 0.0, 0.2), (50, 0.5, 0.02, 0.3), (100, 0.3, 0.05, 0.2)],
)
def test_the_paired_intervals_cover_a_real_difference(n: int, p_both: float, p_base_only: float, p_treat_only: float) -> None:
    truth = p_treat_only - p_base_only
    tables = _simulate(n, p_both, p_base_only, p_treat_only, draws=600, seed=n)
    bp = sum(bonett_price_paired(b, c, n)[0] <= truth <= bonett_price_paired(b, c, n)[1] for _a, b, c, _d in tables)
    nc = sum(newcombe_paired(a, b, c, d)[0] <= truth <= newcombe_paired(a, b, c, d)[1] for a, b, c, d in tables)
    assert bp / len(tables) >= 0.93
    assert nc / len(tables) >= 0.93


# --- a mean of differences ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("df", "expected"),
    [(1, 12.706205), (2, 4.302653), (7, 2.364624), (22, 2.073873), (31, 2.039513), (100, 1.983972)],
)
def test_t_quantile_matches_the_table(df: int, expected: float) -> None:
    assert t_quantile(0.975, df) == pytest.approx(expected, abs=1e-6)


def test_t_quantile_is_antisymmetric_and_finds_far_tails() -> None:
    assert t_quantile(0.025, 7) == pytest.approx(-2.364624, abs=1e-6)
    assert t_quantile(0.995, 1) == pytest.approx(63.656741, abs=1e-5)
    assert t_quantile(0.5, 9) == pytest.approx(0.0, abs=1e-9)


def test_t_cdf_matches_the_table_on_both_sides() -> None:
    assert t_cdf(0.0, 5) == 0.5
    assert t_cdf(2.570582, 5) == pytest.approx(0.975, abs=1e-6)
    assert t_cdf(-2.570582, 5) == pytest.approx(0.025, abs=1e-6)
    assert t_cdf(1.0, 3) == pytest.approx(0.804499, abs=1e-6)  # x below the symmetry switch
    assert t_cdf(0.2, 30) == pytest.approx(0.578584, abs=1e-6)  # x above it


def test_t_functions_refuse_nonsense() -> None:
    with pytest.raises(ValueError):
        t_cdf(1.0, 0)
    for bad in (0.0, 1.0):
        with pytest.raises(ValueError):
            t_quantile(bad, 5)


def test_mean_t_interval_matches_the_hand_computed_interval() -> None:
    mean, low, high = mean_t_interval([1.0, 2.0, 3.0])
    half = 4.302653 / math.sqrt(3)
    assert mean == 2.0
    assert (low, high) == pytest.approx((2.0 - half, 2.0 + half), abs=1e-6)
    _, low90, high90 = mean_t_interval([1.0, 2.0, 3.0], alpha=0.10)
    assert high90 - low90 < high - low


def test_mean_t_interval_does_not_invent_a_spread() -> None:
    mean, low, high = mean_t_interval([0.4])
    assert mean == 0.4 and low == -math.inf and high == math.inf
    mean, low, high = mean_t_interval([])
    assert math.isnan(mean) and low == -math.inf and high == math.inf
    with pytest.raises(ValueError):
        mean_t_interval([1.0, 2.0], alpha=0.0)
    with pytest.raises(ValueError):
        mean_t_interval([1.0, 2.0], alpha=1.0)


# --- equivalence ----------------------------------------------------------------------------------


def test_equivalence_reads_both_sides_and_non_inferiority_reads_one() -> None:
    inside = Equivalence(-0.03, 0.04, margin=0.05, alpha=0.05)
    assert inside.equivalent and inside.non_inferior
    above = Equivalence(-0.03, 0.08, margin=0.05, alpha=0.05)
    assert not above.equivalent and above.non_inferior
    below = Equivalence(-0.06, 0.01, margin=0.05, alpha=0.05)
    assert not below.equivalent and not below.non_inferior
    on_the_edge = Equivalence(-0.05, 0.05, margin=0.05, alpha=0.05)
    assert not on_the_edge.equivalent and not on_the_edge.non_inferior  # strict inequalities


def test_tost_paired_reads_the_ninety_percent_bonett_price_interval() -> None:
    result = tost_paired(3, 4, 200, margin=0.05)
    low, high = bonett_price_paired(3, 4, 200, z=1.6448536269514722)
    assert (result.low, result.high) == pytest.approx((low, high), abs=1e-12)
    assert result.equivalent
    assert not tost_paired(3, 4, 40, margin=0.05).equivalent  # same split, too few pairs


def test_tost_unpaired_reads_the_ninety_percent_newcombe_interval() -> None:
    result = tost_unpaired(150, 200, 148, 200, margin=0.10)
    low, high = newcombe_unpaired(150, 200, 148, 200, z=1.6448536269514722)
    assert (result.low, result.high) == pytest.approx((low, high), abs=1e-12)
    assert result.equivalent and result.margin == 0.10 and result.alpha == 0.05


def test_tost_mean_reads_the_ninety_percent_t_interval() -> None:
    values = [0.01, -0.02, 0.0, 0.015, -0.005, 0.02, -0.01, 0.0]
    result = tost_mean(values, margin=0.05)
    _, low, high = mean_t_interval(values, alpha=0.10)
    assert (result.low, result.high) == pytest.approx((low, high), abs=1e-12)
    assert result.equivalent
    assert not tost_mean(values, margin=0.01).equivalent


def test_tost_refuses_a_margin_or_alpha_it_cannot_read() -> None:
    for bad_margin in (0.0, -0.1):
        with pytest.raises(ValueError):
            tost_paired(1, 1, 10, margin=bad_margin)
    for bad_alpha in (0.0, 0.5):
        with pytest.raises(ValueError):
            tost_unpaired(1, 10, 1, 10, margin=0.1, alpha=bad_alpha)
        with pytest.raises(ValueError):
            tost_mean([0.1, 0.2], margin=0.1, alpha=bad_alpha)


# --- boundaries the mutation gate asked about -----------------------------------------------------


def test_a_single_trial_still_gets_a_real_interval() -> None:
    assert wilson(1, 1) == pytest.approx((0.206549, 1.0), abs=1e-6)
    assert wilson(0, 1) == pytest.approx((0.0, 0.793451), abs=1e-6)
    assert newcombe_unpaired(1, 1, 0, 5) == pytest.approx((0.095379, 1.0), abs=1e-6)
    assert newcombe_unpaired(0, 5, 1, 1) == pytest.approx((-1.0, -0.095379), abs=1e-6)
    assert bonett_price_paired(0, 1, 1) == pytest.approx((-0.733536, 1.0), abs=1e-6)
    assert newcombe_paired(0, 0, 1, 0) == pytest.approx((-0.122109, 1.0), abs=1e-6)
    assert conditional_wilson_paired(0, 1, 1) == pytest.approx((-0.586901, 1.0), abs=1e-6)
    assert conditional_wilson_paired(0, 1, 10) == pytest.approx((-0.058690, 0.1), abs=1e-6)


def test_the_clamps_catch_the_rounding_that_crosses_the_unit_interval() -> None:
    # The raw Wilson bounds land at 1.0000000000000002 and -6.9e-18 here; a probability is printed.
    assert wilson(16, 16)[1] == 1.0
    assert wilson(0, 27)[0] == 0.0
    # Bonett-Price's Wald step does leave [-1, 1] for real, not just by rounding.
    assert bonett_price_paired(2, 0, 2) == pytest.approx((-1.0, 0.348689), abs=1e-6)
    assert bonett_price_paired(2, 0, 2)[0] == -1.0


def test_every_interval_honours_the_z_it_is_given() -> None:
    assert newcombe_unpaired(1, 1, 0, 5, z=1.0) == pytest.approx((0.472954, 1.0), abs=1e-6)
    assert newcombe_paired(3, 2, 5, 20, z=1.0)[1] < newcombe_paired(3, 2, 5, 20)[1]
    assert newcombe_paired(3, 2, 5, 20, z=1.0)[0] > newcombe_paired(3, 2, 5, 20)[0]
    assert conditional_wilson_paired(3, 5, 20) == pytest.approx((-0.155406, 0.290525), abs=1e-6)
    assert conditional_wilson_paired(3, 5, 20, z=1.0) == pytest.approx((-0.040688, 0.218466), abs=1e-6)


def test_newcombe_paired_takes_each_branch_of_the_phi_correction() -> None:
    # Values cross-checked against an independent implementation of method 10.
    # ad - bc = 50 > n/2 = 15: corrected to 35.
    assert newcombe_paired(3, 2, 5, 20) == pytest.approx((-0.084885, 0.280258), abs=1e-6)
    # ad - bc = 3, between n/3 and n/2 = 3.5: corrected to 0, not to a negative number.
    assert newcombe_paired(1, 1, 1, 4) == pytest.approx((-0.409493, 0.409493), abs=1e-6)


def test_mean_t_interval_reads_two_values() -> None:
    mean, low, high = mean_t_interval([1.0, 3.0])
    assert mean == 2.0
    assert (low, high) == pytest.approx((2.0 - 12.706205, 2.0 + 12.706205), abs=1e-5)


def test_t_cdf_is_exact_at_the_ends_and_where_the_direct_fraction_would_fail() -> None:
    assert t_cdf(math.inf, 5) == 1.0
    assert t_cdf(-math.inf, 5) == 0.0
    # Large df and a t near zero: x sits next to 1, where only the symmetric branch converges.
    assert t_cdf(0.01, 1000) == pytest.approx(0.503988, abs=1e-6)
    assert t_cdf(1.415, 2000) == pytest.approx(0.921388, abs=1e-6)


def test_the_tost_results_carry_the_alpha_they_were_read_at() -> None:
    assert tost_paired(3, 4, 200, margin=0.05).alpha == 0.05
    assert tost_paired(3, 4, 200, margin=0.05, alpha=0.025).alpha == 0.025
    assert tost_mean([0.0, 0.01, -0.01], margin=0.5, alpha=0.1).alpha == 0.1


# --- a median, an AUROC ---------------------------------------------------------------------------


def test_median_interval_uses_the_binomial_order_statistics() -> None:
    # n = 22: P(Bin(22, 1/2) <= 5) = 0.0085 <= 0.025 < P(<= 6), so the 6th and 17th values.
    assert median_interval(list(range(1, 23))) == (11.5, 6, 17)
    # n = 6: only the extremes reach 95% (P(<= 0) = 1/64).
    assert median_interval([6, 1, 5, 2, 4, 3]) == (3.5, 1, 6)
    # n = 7 at 80%: P(<= 1) = 8/128 <= 0.10 < P(<= 2), so the 2nd and 6th values.
    assert median_interval([1, 2, 3, 4, 5, 6, 7], alpha=0.2) == (4, 2, 6)


def test_median_interval_does_not_overstate_a_handful() -> None:
    assert median_interval([1, 2, 3, 4, 5]) == (3, -math.inf, math.inf)  # no pair reaches 95%
    assert median_interval([5.0]) == (5.0, -math.inf, math.inf)
    median, low, high = median_interval([])
    assert math.isnan(median) and low == -math.inf and high == math.inf
    for bad in (0.0, 1.0):
        with pytest.raises(ValueError):
            median_interval([1, 2, 3], alpha=bad)


def test_auroc_hanley_mcneil_reproduces_the_paper_example() -> None:
    # Hanley & McNeil (1982): AUC 0.893 on 51 abnormal and 58 normal cases has SE 0.032.
    low, high = auroc_hanley_mcneil(0.893, 51, 58)
    assert (high - low) / (2 * Z95) == pytest.approx(0.0325, abs=5e-4)
    assert (low, high) == pytest.approx((0.829278, 0.956722), abs=1e-6)


def test_auroc_hanley_mcneil_is_clamped_and_empty_is_no_information() -> None:
    assert auroc_hanley_mcneil(0.99, 3, 3) == pytest.approx((0.897731, 1.0), abs=1e-6)
    assert auroc_hanley_mcneil(0.02, 3, 3) == pytest.approx((0.0, 0.150289), abs=1e-6)
    assert auroc_hanley_mcneil(0.8, 0, 5) == (0.0, 1.0)
    assert auroc_hanley_mcneil(0.8, 5, 0) == (0.0, 1.0)


def test_welch_interval_matches_the_hand_computed_interval() -> None:
    # Means 3 and 4, variances 2.5 and 4, n 5 and 3: se^2 = 0.5 + 4/3, Welch df = 3.533.
    diff, low, high = welch_t_interval([1, 2, 3, 4, 5], [2, 4, 6])
    se = math.sqrt(0.5 + 4 / 3)
    df = (0.5 + 4 / 3) ** 2 / (0.25 / 4 + (4 / 3) ** 2 / 2)
    assert diff == -1.0
    assert (low, high) == pytest.approx((-1.0 - t_quantile(0.975, df) * se, -1.0 + t_quantile(0.975, df) * se), abs=1e-9)
    assert (low, high) == pytest.approx((-4.963726, 2.963726), abs=1e-6)
    _, low90, high90 = welch_t_interval([1, 2, 3, 4, 5], [2, 4, 6], alpha=0.10)
    assert low90 > low and high90 < high


def test_welch_interval_without_a_spread_says_so() -> None:
    # No variance at all. This used to pin (-1.0, -1.0, -1.0): a zero-width interval, the certainty
    # from two items per group that the module's own rule refuses for one item. Undefined instead.
    assert welch_t_interval([1.0, 1.0], [2.0, 2.0]) == (-1.0, -math.inf, math.inf)
    diff, low, high = welch_t_interval([1.0], [2.0, 3.0])
    assert diff == -1.5 and low == -math.inf and high == math.inf
    diff, low, high = welch_t_interval([1.0, 2.0], [3.0])
    assert diff == -1.5 and low == -math.inf and high == math.inf
    diff, low, high = welch_t_interval([], [1.0, 2.0])
    assert math.isnan(diff) and low == -math.inf and high == math.inf
    diff, _, _ = welch_t_interval([1.0, 2.0], [])
    assert math.isnan(diff)
    for bad in (0.0, 1.0):
        with pytest.raises(ValueError):
            welch_t_interval([1.0, 2.0], [3.0, 4.0], alpha=bad)


def test_newcombe_paired_passes_z_to_both_marginals() -> None:
    # Cross-checked against an independent method-10 implementation at z = 1.
    assert newcombe_paired(3, 2, 5, 20, z=1.0) == pytest.approx((0.006664, 0.192336), abs=1e-6)


def test_median_interval_edges_of_the_binomial_rule() -> None:
    # n = 9: P(<= 1) = 0.0195 <= 0.025 < P(<= 2), so the 2nd and 8th values.
    assert median_interval(list(range(1, 10))) == (5, 2, 8)
    # A tail exactly equal to alpha/2 is inside the rule (coverage is "at least 1 - alpha").
    assert median_interval([1, 2, 3, 4, 5, 6], alpha=0.03125) == (3.5, 1, 6)


def test_auroc_hanley_mcneil_reads_a_class_of_one() -> None:
    assert auroc_hanley_mcneil(0.8, 1, 5) == pytest.approx((0.215652, 1.0), abs=1e-6)
    assert auroc_hanley_mcneil(0.8, 5, 1) == pytest.approx((0.347366, 1.0), abs=1e-6)


def test_mean_t_interval_checks_alpha_before_it_looks_at_the_values() -> None:
    with pytest.raises(ValueError):
        mean_t_interval([0.4], alpha=0.0)


def test_welch_interval_squares_both_standard_deviations() -> None:
    # A second group whose SD is not 2 (where SD * 2 and SD ** 2 coincide).
    # se^2 = 0.5 + (7/3)/3, Welch df = 4.47, t = 2.665: checked by hand to the third decimal.
    assert welch_t_interval([1, 2, 3, 4, 5], [1, 2, 4]) == pytest.approx((0.666667, -2.345010, 3.678343), abs=1e-6)


def test_welch_interval_checks_alpha_before_it_looks_at_the_values() -> None:
    with pytest.raises(ValueError):
        welch_t_interval([1.0], [2.0], alpha=0.0)


def test_the_uncorrected_phi_reproduces_what_the_drifted_copies_printed() -> None:
    # Newcombe's worked example without his correction: the narrower 0.0182 to 0.2892 the
    # sharded_recap / spoken_standard / unattended_claims copies printed, against the paper's
    # 0.0112 to 0.2954. Kept only to reproduce published numbers before re-reading them.
    assert newcombe_paired(12, 2, 9, 21, phi_correction=False) == pytest.approx((0.018210, 0.289157), abs=1e-6)
    assert newcombe_paired(12, 2, 9, 21) == pytest.approx((0.0112, 0.2954), abs=5e-5)


def test_mover_with_two_wilsons_is_newcombe_unpaired() -> None:
    diff, low, high = mover_difference(0.9, wilson(9, 10), 0.1, wilson(1, 10))
    assert diff == pytest.approx(0.8, abs=1e-12)
    assert (low, high) == pytest.approx(newcombe_unpaired(9, 10, 1, 10), abs=1e-12)


def test_mover_keeps_an_asymmetric_interval_asymmetric() -> None:
    diff, low, high = mover_difference(0.5, (0.4, 0.8), 0.1, (0.05, 0.12))
    assert diff == pytest.approx(0.4)
    assert low == pytest.approx(0.4 - math.sqrt(0.1**2 + 0.02**2))
    assert high == pytest.approx(0.4 + math.sqrt(0.3**2 + 0.05**2))


def test_values_with_no_spread_give_an_undefined_interval_not_a_zero_width_one() -> None:
    # governance_axes printed "t over 3 tasks [71.4%, 71.4%]": three identical shares read as a
    # certain 71.4%. Two or three equal values carry no spread, exactly like one value.
    for values in ([0.5, 0.5], [0.5, 0.5, 0.5], [5 / 7] * 3):
        mean, low, high = mean_t_interval(values)
        assert mean == pytest.approx(values[0])
        assert (low, high) == (-math.inf, math.inf)
    assert not tost_mean([0.0, 0.0, 0.0], margin=0.05).equivalent  # no spread is not equivalence


# --- association between two binary outcomes (governance_axes axis 2) ---------------------------


def test_phi_and_fisher_reproduce_the_tea_tasting_table() -> None:
    # Fisher's lady tasting tea: 3 cups right of each kind, 1 wrong. phi = (9 - 1) / sqrt(4^4) = 0.5,
    # and the one-sided p is (C(4,3)C(4,1) + C(4,4)C(4,0)) / C(8,4) = 17/70.
    assert phi_coefficient(3, 1, 1, 3) == 0.5
    assert fisher_exact_greater(3, 1, 1, 3) == pytest.approx(17 / 70, abs=1e-15)


def test_phi_and_fisher_on_an_asymmetric_table() -> None:
    # Margins 5 and 3 over n = 10: phi = (2*4 - 3*1) / sqrt(5*5*3*7) = 5 / sqrt(525).
    assert phi_coefficient(2, 3, 1, 4) == pytest.approx(5 / math.sqrt(525), abs=1e-15)
    # P(X >= 2), X ~ Hypergeometric(N=10, K=3, draws=5): (C(3,2)C(7,3) + C(3,3)C(7,2)) / C(10,5).
    assert fisher_exact_greater(2, 3, 1, 4) == pytest.approx((3 * 35 + 1 * 21) / 252, abs=1e-15)
    # The roles of the two outcomes are symmetric; swapping them changes neither number.
    assert phi_coefficient(2, 1, 3, 4) == phi_coefficient(2, 3, 1, 4)
    assert fisher_exact_greater(2, 1, 3, 4) == pytest.approx(fisher_exact_greater(2, 3, 1, 4), abs=1e-15)


def test_phi_is_negative_when_the_outcomes_avoid_each_other_and_fisher_says_so() -> None:
    assert phi_coefficient(0, 4, 4, 0) == -1.0
    assert fisher_exact_greater(0, 4, 4, 0) == 1.0  # at least zero joint items: certain
    assert fisher_exact_greater(4, 0, 0, 4) == pytest.approx(1 / 70, abs=1e-15)  # the most extreme table


def test_phi_without_a_margin_is_undefined_not_zero() -> None:
    assert math.isnan(phi_coefficient(0, 0, 3, 5))  # the first outcome never happens
    assert math.isnan(phi_coefficient(2, 3, 0, 0))  # the second outcome always happens
    assert fisher_exact_greater(0, 0, 3, 5) == 1.0


def test_the_joint_failure_table_governance_axes_published() -> None:
    # OATS, 64 attacks: both layers miss 3, only L1 misses 31, only L2 misses 0, neither 30.
    assert phi_coefficient(3, 31, 0, 30) == pytest.approx(0.20831324236136575, abs=1e-12)
    assert fisher_exact_greater(3, 31, 0, 30) == pytest.approx(0.1436251920122888, abs=1e-12)
