"""A trial that stopped short is not a trial that failed.

`ReplicatedArm` scored every trial as pass or fail, so a budget cap, a wall-clock timeout or a
provider outage landed in the denominator as a loss. The learning-lift series paid for this once
already (run 7a: a swallowed timeout read as capability loss), and SaltBench (arXiv 2609.11076, read
in the 2026-09-11 sweep, item C7) makes it a rule of the protocol: *a budget stop is a halt, never a
failure*. `halted` is the mask; a halted trial leaves every rate, a task whose every run halted is
unmeasured and named, and a task unmeasured in either arm leaves the pairing.

With no mask, every number is exactly what it was — asserted, because the callers in
`chimera/eval/scenarios.py` pass none.
"""

from __future__ import annotations

import pytest

from chimera.eval.replicated import ReplicatedArm, compare_replicated, format_replicated_report


def test_without_a_mask_nothing_changes() -> None:
    arm = ReplicatedArm("a", [[True, True], [True, False], [False, False]])
    assert arm.n == 3 and arm.pass_at_1 == pytest.approx(3 / 6) and arm.pass_pow_k == pytest.approx(1 / 3)
    assert arm.flip_rate == pytest.approx(1 / 3)
    assert arm.halted_trials == 0 and arm.unmeasured_tasks == 0
    assert arm.summary()["halted_trials"] == 0


def test_a_halted_trial_leaves_every_rate() -> None:
    # Task 0: one pass, one halt → measured on the pass alone. Task 1: two passes. Task 2: fail + halt.
    arm = ReplicatedArm(
        "a",
        runs=[[True, False], [True, True], [False, False]],
        halted=[[False, True], [False, False], [False, True]],
    )
    assert arm.halted_trials == 2 and arm.unmeasured_tasks == 0 and arm.n == 3
    # Finished trials: task0 [T], task1 [T, T], task2 [F] → pass@1 = 3/4, not 3/6.
    assert arm.pass_at_1 == pytest.approx(3 / 4)
    # pass^k over finished runs: task0 T, task1 T, task2 F.
    assert arm.per_task_pow_k == [True, True, False]
    assert arm.pass_pow_k == pytest.approx(2 / 3)
    # Nothing flipped among finished runs — the halt is not a disagreement.
    assert arm.flip_rate == 0.0


def test_a_task_whose_every_run_halted_is_unmeasured_not_zero() -> None:
    arm = ReplicatedArm("a", runs=[[False, False], [True, True]], halted=[[True, True], [False, False]])
    assert arm.unmeasured_tasks == 1 and arm.n == 1
    assert arm.pass_pow_k == 1.0, "the halted task is not a 0% row"
    assert arm.per_task_pow_k == [True]
    assert arm.summary()["unmeasured_tasks"] == 1


def test_a_task_unmeasured_in_one_arm_leaves_the_pairing_and_is_counted() -> None:
    base = ReplicatedArm("base", runs=[[True], [True], [False]])
    treat = ReplicatedArm("treat", runs=[[True], [False], [True]], halted=[[False], [True], [False]])
    result = compare_replicated(base, treat)
    assert result.excluded_tasks == 1
    assert result.paired.n == 2
    # The two remaining tasks: (T, T) and (F, T) → one treatment-only win, no baseline-only.
    assert result.paired.treatment_only == 1 and result.paired.baseline_only == 0
    report = format_replicated_report(result)
    assert "1 trial(s) stopped short" in report and "1 task(s) unmeasured in one arm" in report


def test_the_mask_must_match_the_shape() -> None:
    with pytest.raises(ValueError, match="halted"):
        ReplicatedArm("a", runs=[[True, True]], halted=[[True]])
