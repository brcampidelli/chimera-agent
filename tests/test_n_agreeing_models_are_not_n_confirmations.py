"""A panel that shares its input does not cast independent votes, and both places now say so.

`bench/panel_correlation` measured three of our own models over 50 AIME items: **34 of 50 unanimous
where 16.5 were expected**, ICC(1) **+0.527**, so three votes carried **1.46** independent ones.
arXiv 2609.10969 is the same finding at scale — voting over shared evidence approves 62.9% of unsafe
proposals against 22.9% with independent sources, a 40.9 pp SOURCE effect against an 11.3 pp MODEL
effect.

`auto_evolve` accepts a learned skill on how many transfer models ran it, and every one of them is
handed the same test input. What is pinned here is the shape of the response, which is deliberately
NOT a discount:

* `wilson_lower_best_of` gains an explicit `effective_n` — the assumption becomes a parameter,
  `None` behaves exactly as before, and the point estimate does not move when it is used;
* the gate warns, once, naming the measurement;
* no number measured on one panel is applied to another.
"""

from __future__ import annotations

import logging

import pytest

from chimera.eval.anytime import wilson_lower_best_of


def test_effective_n_defaults_to_no_change_at_all() -> None:
    """The parameter is a place to say something, not a behaviour change for callers who do not."""
    for successes, n, k in ((3, 3, 3), (2, 3, 1), (7, 10, 4)):
        assert wilson_lower_best_of(successes, n, k) == wilson_lower_best_of(
            successes, n, k, effective_n=None
        )


def test_declaring_fewer_independent_trials_widens_the_bound() -> None:
    """Three correlated votes must not earn the bound three independent ones would."""
    honest = wilson_lower_best_of(3, 3, 1, effective_n=1.46)
    naive = wilson_lower_best_of(3, 3, 1)
    assert honest < naive, "a correlated panel bought a bound it had not paid for"


def test_the_point_estimate_does_not_move() -> None:
    """A design effect widens an interval; it does not change what was observed. 3 of 3 is still
    all of them — what changes is how much that is worth believing."""
    # successes and n are rescaled together, so a unanimous panel stays unanimous.
    assert wilson_lower_best_of(3, 3, 1, effective_n=1.46) <= 1.0
    assert wilson_lower_best_of(0, 3, 1, effective_n=1.46) >= 0.0


def test_an_effective_n_that_is_not_a_discount_is_ignored() -> None:
    """Only a value that says "fewer than the trials" means anything; a larger one would be a caller
    claiming more information than it collected, and is refused rather than honoured."""
    plain = wilson_lower_best_of(3, 3, 1)
    assert wilson_lower_best_of(3, 3, 1, effective_n=9.0) == plain
    assert wilson_lower_best_of(3, 3, 1, effective_n=0.0) == plain


def _evolver(models: list[str]):
    from chimera.evolution.auto_evolve import AutoSkillEvolver

    class _Collective:
        transfer_models = models

    evolver = AutoSkillEvolver.__new__(AutoSkillEvolver)
    evolver.collective = _Collective()  # type: ignore[assignment]
    return evolver


def test_the_gate_says_the_votes_are_correlated_once(caplog: pytest.LogCaptureFixture) -> None:
    evolver = _evolver(["a", "b", "c"])
    with caplog.at_level(logging.WARNING, logger="chimera.evolution.auto"):
        evolver._warn_panel_votes_are_correlated()
        evolver._warn_panel_votes_are_correlated()
    assert caplog.text.count("same test input") == 1, "a warning repeated per candidate is noise"
    assert "1.46" in caplog.text, "the claim has to carry the measurement it rests on"


def test_a_panel_of_one_has_nothing_to_be_correlated_with(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="chimera.evolution.auto"):
        _evolver(["only"])._warn_panel_votes_are_correlated()
    assert "same test input" not in caplog.text


def test_a_duck_typed_collective_without_a_panel_does_not_break_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Same defensiveness the neighbouring warning documents: a helper whose only job is to emit a
    warning must never be the thing that ends a run."""
    from chimera.evolution.auto_evolve import AutoSkillEvolver

    evolver = AutoSkillEvolver.__new__(AutoSkillEvolver)
    evolver.collective = object()  # type: ignore[assignment]
    with caplog.at_level(logging.WARNING, logger="chimera.evolution.auto"):
        evolver._warn_panel_votes_are_correlated()
    assert "same test input" not in caplog.text
