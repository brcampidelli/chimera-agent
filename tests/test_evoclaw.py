"""EvoClaw stress test: the guarded regime must resist degradation the naive one suffers."""

from __future__ import annotations

import re

from chimera.eval import compare, counter_chain, run_guarded, run_naive


class FlakyCounter:
    """Increments the number in the prompt, but every ``fail_every``-th call returns
    a wrong value — a transient error that *propagates* without countermeasures."""

    def __init__(self, fail_every: int = 3) -> None:
        self.fail_every = fail_every
        self.calls = 0

    def solve(self, prompt: str) -> str:
        self.calls += 1
        n = int(re.search(r"number (\d+)", prompt).group(1))  # type: ignore[union-attr]
        if self.fail_every and self.calls % self.fail_every == 0:
            return str(n)  # WRONG: forgot to add 1
        return str(n + 1)


class AlwaysWrong:
    def solve(self, prompt: str) -> str:
        return "0"


def test_guarded_resists_propagation_that_sinks_naive() -> None:
    steps = counter_chain(12)
    naive = run_naive(FlakyCounter(3), steps, initial_state="0")
    guarded = run_guarded(FlakyCounter(3), steps, initial_state="0", max_retries=2)

    # One bad step corrupts the running value and cascades in the naive regime...
    assert naive.pass_rate < 0.5
    # ...but the guard retries from the last good state, so the chain stays on track.
    assert guarded.pass_rate == 1.0
    assert guarded.pass_rate > naive.pass_rate
    assert naive.degradation > guarded.degradation


def test_compare_reports_a_positive_degradation_gap() -> None:
    comp = compare(lambda: FlakyCounter(3), counter_chain(12), initial_state="0", max_retries=2)
    assert comp.naive.pass_rate < comp.guarded.pass_rate
    assert comp.degradation_gap > 0  # the guard reduces degradation


def test_perfect_solver_makes_both_regimes_pass() -> None:
    comp = compare(lambda: FlakyCounter(0), counter_chain(6), initial_state="0")
    assert comp.naive.pass_rate == 1.0
    assert comp.guarded.pass_rate == 1.0
    assert comp.degradation_gap == 0.0


def test_guard_is_not_magic_when_every_attempt_fails() -> None:
    # The guard can't rescue a solver that never produces a valid step.
    report = run_guarded(AlwaysWrong(), counter_chain(3), initial_state="0", max_retries=1)
    assert report.pass_rate == 0.0
