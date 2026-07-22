"""Picking the best of k candidates has to be paid for in the bound that judges the winner."""

from __future__ import annotations

import random

from chimera.eval.anytime import (
    Z95,
    best_possible_wilson,
    wilson_lower,
    wilson_lower_best_of,
    z_for,
)


def test_z_for_matches_the_z95_constant() -> None:
    assert abs(z_for(0.05) - Z95) < 1e-9


def test_selecting_from_more_candidates_tightens_the_bound() -> None:
    single = wilson_lower_best_of(3, 3, k=1)
    best_of_three = wilson_lower_best_of(3, 3, k=3)
    best_of_ten = wilson_lower_best_of(3, 3, k=10)

    assert single > best_of_three > best_of_ten
    # k <= 1 is exactly the ordinary bound — no correction to pay.
    assert abs(single - wilson_lower(3, 3)) < 1e-12


def test_the_default_wilson_gate_is_unsatisfiable_and_says_so() -> None:
    """The finding that motivated this: at n=3 even a flawless run cannot clear 0.5."""
    assert best_possible_wilson(3, k=1) < 0.5
    assert best_possible_wilson(3, k=3) < 0.5
    # A larger panel earns the threshold honestly rather than by weakening it.
    assert best_possible_wilson(12, k=3) > 0.5


def test_correction_lowers_the_false_accept_rate_of_a_best_of_k_gate() -> None:
    """The point of the correction, measured rather than asserted.

    Three useless candidates (true pass rate 0.3) are scored on 3 trials each and the best is put
    against a threshold it should not clear. The corrected bound must accept strictly less often.
    """
    rng = random.Random(20260722)
    trials, k, n, p_true, threshold = 4000, 3, 3, 0.3, 0.20

    def accepted(corrected: bool) -> int:
        hits = 0
        for _ in range(trials):
            best = -1.0
            for _ in range(k):
                passed = sum(rng.random() < p_true for _ in range(n))
                score = wilson_lower_best_of(passed, n, k) if corrected else wilson_lower(passed, n)
                best = max(best, score)
            hits += best >= threshold
        return hits

    assert accepted(corrected=True) < accepted(corrected=False)
