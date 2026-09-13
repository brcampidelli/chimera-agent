"""`design_effect`, and the sentence the replicated report now prints beside its `k`.

`bench/design_effect/RESULTS.md` measured this project's own intra-task correlation at ICC(1) 0.706
— higher than the 0.530 of the paper that prompted it — which means three runs of a task carry 1.24
independent observations rather than three. The report used to say "k=3: decides" and stop, which
invites reading three runs as three observations.

What is pinned here is the arithmetic and, more importantly, the two places it must NOT be applied:
a `None` ICC prices nothing, and a negative one is not a bonus.
"""

from __future__ import annotations

from chimera.eval.replicated import (
    ReplicatedArm,
    compare_replicated,
    design_effect,
    format_replicated_report,
)

MEASURED_ICC = 0.7061
"""bench/design_effect/RESULTS.md, median of the factorial's eight arms."""


def test_one_run_per_task_is_one_observation() -> None:
    """A cluster of one is never discounted, whatever the correlation."""
    assert design_effect(0.9, 1) == 1.0
    assert design_effect(0.0, 1) == 1.0


def test_uncorrelated_replicas_are_worth_their_count() -> None:
    assert design_effect(0.0, 3) == 1.0
    assert design_effect(0.0, 8) == 1.0


def test_our_measured_correlation_makes_three_runs_worth_about_one_and_a_quarter() -> None:
    deff = design_effect(MEASURED_ICC, 3)
    assert deff is not None
    assert round(3 / deff, 2) == 1.24, (
        "the number RESULTS.md and PROTOCOL.md §8 both quote; if this moves, they are stale"
    )


def test_a_negative_correlation_is_not_a_bonus() -> None:
    """Clamped at 0. Without the clamp the formula returns a design effect BELOW 1, which claims a
    cluster is worth more than its members — and a negative ICC means "the runs within a task
    disagree more than the tasks do", which is a statement about noise, not a discount to spend."""
    assert design_effect(-0.4, 3) == 1.0


def test_an_icc_that_could_not_be_computed_prices_nothing() -> None:
    """`None` in, `None` out. `icc1` returns None rather than 0.0 when the grid cannot support the
    statistic, and treating that as "uncorrelated" would silently price replicas at full value on
    exactly the runs that could not say."""
    assert design_effect(None, 3) is None


def test_the_report_says_what_the_kth_run_bought() -> None:
    # Three tasks, three runs each, built so the arms have a real ICC to price: tasks differ from
    # each other and runs within a task agree.
    baseline = ReplicatedArm("base", [[True, True, True], [False, False, False], [True, True, True]])
    treatment = ReplicatedArm("treat", [[True, True, True], [False, False, False], [True, True, True]])
    text = format_replicated_report(compare_replicated(baseline, treatment))
    assert "what k bought" in text
    assert "independent observations, not 3" in text


def test_the_report_says_so_when_there_is_no_icc_to_price_with() -> None:
    """Every trial identical means no variance anywhere, so `icc1` abstains — and the line has to
    say that instead of printing a discount it did not compute."""
    flat = [[True, True, True], [True, True, True]]
    text = format_replicated_report(
        compare_replicated(ReplicatedArm("base", flat), ReplicatedArm("treat", flat))
    )
    assert "no ICC to price it with" in text
