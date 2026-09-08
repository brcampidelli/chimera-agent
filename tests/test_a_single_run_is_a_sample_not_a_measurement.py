"""A single run of an agent benchmark is a sample. This is the ruler that makes it a measurement.

Anchored on real data: `bench/loopsbench` p5 and p6, the identical configuration run twice on the
same eight tasks. Each resolved exactly one task, and a different one. That grid is reproduced here
as a fixture so the numbers this module reports for it are pinned — pass@1 12.5%, pass^2 0%, flip
rate 25%, ICC(1) negative — and cannot drift quietly.

Every guard below was reverted on disk and its test confirmed red.
"""

from __future__ import annotations

import pytest

from chimera.eval.replicated import (
    ReplicatedArm,
    compare_replicated,
    format_replicated_report,
    icc1,
    seeds_verdict,
)

# The eight LoopsBench pilot tasks, alphabetical, as p5 and p6 graded them. Only two rows differ
# between the runs, and they are different rows: that is the whole finding.
P5_P6 = [
    [False, False],  # task_compiler_fdmj_llvm
    [False, False],  # task_cs61b_extra_java_bundle
    [False, False],  # task_db_storage_index_labs
    [False, False],  # task_dbcompiler
    [True, False],  # task_ml_four_assignments  — resolved in p5 (16/16 tests), failed in p6
    [False, False],  # task_os_c_fs_labs
    [False, True],  # task_sql_engine_myjql     — ungraded in p5, resolved in p6
    [False, False],  # task_xjqkl
]


# --- the real anchor --------------------------------------------------------------------------


def test_the_loopsbench_repeat_pinned() -> None:
    """Same rate twice, zero overlap: pass@1 holds at 12.5% while pass^2 is 0 and ICC is negative."""
    arm = ReplicatedArm("chimera", P5_P6)
    assert arm.n == 8 and arm.k == 2
    assert arm.pass_at_1 == pytest.approx(0.125)
    assert arm.pass_pow_k == 0.0
    assert arm.flip_rate == pytest.approx(0.25)
    icc = arm.icc
    assert icc is not None
    # Within-task spread exceeds between-task spread: which task passes is not a task property.
    assert icc < 0.0
    assert icc == pytest.approx(-0.0769, abs=1e-3)


def test_a_single_run_reads_the_same_rate_and_hides_all_of_it() -> None:
    """The number p5 alone reported is the pass@1 of the pair; nothing else survives k=1."""
    one = ReplicatedArm("p5-only", [[row[0]] for row in P5_P6])
    assert one.pass_at_1 == pytest.approx(0.125)
    assert one.pass_pow_k == pytest.approx(0.125)  # with k=1 pass^k IS pass@1 — the illusion
    assert one.flip_rate == 0.0  # a single run cannot flip, so it reports no noise at all
    assert one.icc is None and "two runs" in one.icc_reason


# --- pass^k is strict --------------------------------------------------------------------------


def test_pass_pow_k_requires_every_run_to_pass() -> None:
    arm = ReplicatedArm("a", [[True, True, True], [True, True, False], [False, False, False]])
    assert arm.pass_pow_k == pytest.approx(1 / 3)
    assert arm.pass_at_1 == pytest.approx(5 / 9)


def test_flip_rate_counts_only_mixed_rows() -> None:
    arm = ReplicatedArm("a", [[True, True], [False, False], [True, False], [False, True]])
    assert arm.flip_rate == 0.5


# --- ICC(1) --------------------------------------------------------------------------------------


def test_icc_is_one_when_tasks_are_perfectly_consistent() -> None:
    """All variance between tasks, none within: a task's outcome is a task property."""
    val, reason = icc1([[True, True, True], [False, False, False], [True, True, True]])
    assert reason == "" and val == pytest.approx(1.0)


def test_icc_is_none_not_zero_when_it_cannot_be_computed() -> None:
    assert icc1([[True, False]]) == (None, "needs at least two tasks")
    assert icc1([[True], [False]]) == (None, "needs at least two runs per task")
    val, reason = icc1([[True, True], [True, True]])
    assert val is None and "no variance" in reason


# --- mechanism-active scoring --------------------------------------------------------------------


def test_active_scoring_uses_only_the_trials_where_the_mechanism_fired() -> None:
    runs = [[True, False], [False, False], [True, True]]
    active = [[True, False], [False, False], [True, False]]  # fired on 2 trials, both passed
    arm = ReplicatedArm("retry", runs, active=active)
    assert arm.active_trials == 2
    assert arm.active_pass_rate == 1.0
    assert arm.pass_at_1 == pytest.approx(3 / 6)  # the pooled rate says something else entirely


def test_a_mechanism_that_never_fired_is_not_measured_not_zero() -> None:
    """Zero active trials must not read as 0% — that would say it fired and lost every time."""
    arm = ReplicatedArm("retry", [[True, False], [False, True]], active=[[False, False]] * 2)
    assert arm.active_trials == 0
    assert arm.active_pass_rate is None
    assert "NOT MEASURED" in format_replicated_report(
        compare_replicated(ReplicatedArm("base", [[True, False], [False, True]]), arm)
    )


def test_an_unmarked_arm_reports_no_active_rate_rather_than_pooling() -> None:
    arm = ReplicatedArm("a", [[True, False]])
    assert arm.active_trials is None and arm.active_pass_rate is None


# --- shape guards --------------------------------------------------------------------------------


def test_a_ragged_grid_is_refused() -> None:
    with pytest.raises(ValueError, match="rectangular"):
        ReplicatedArm("a", [[True, True], [True]])


def test_an_active_mask_of_the_wrong_shape_is_refused() -> None:
    with pytest.raises(ValueError, match="same task"):
        ReplicatedArm("a", [[True, True]], active=[[True]])


def test_arms_over_different_task_counts_are_refused() -> None:
    with pytest.raises(ValueError, match="same tasks"):
        compare_replicated(ReplicatedArm("b", [[True]]), ReplicatedArm("t", [[True], [False]]))


# --- the paired comparison rides on the existing ruler -------------------------------------------


def test_the_comparison_is_paired_on_per_task_pass_pow_k() -> None:
    base = ReplicatedArm("base", [[True, True], [False, False], [True, False], [False, False]])
    treat = ReplicatedArm("treat", [[True, True], [True, True], [False, False], [False, False]])
    r = compare_replicated(base, treat)
    # per-task pass^2: base [T,F,F,F], treat [T,T,F,F] -> one treatment-only win, no baseline-only
    assert r.paired.both_pass == 1
    assert r.paired.treatment_only == 1 and r.paired.baseline_only == 0
    assert r.paired.delta == pytest.approx(0.25)


def test_a_delta_no_larger_than_the_flip_rate_is_flagged_inside_the_floor() -> None:
    """The 2606.20695 finding as a guard: seven of ten published effects sat below their own floor."""
    base = ReplicatedArm("base", [[True, False], [False, False], [False, False], [False, False]])
    treat = ReplicatedArm("treat", [[True, True], [False, False], [False, False], [False, False]])
    r = compare_replicated(base, treat)
    assert r.paired.delta == pytest.approx(0.25)  # treatment "wins" one task on pass^2
    assert r.noise_floor == pytest.approx(0.25)  # and the baseline flipped that same fraction alone
    assert r.inside_noise_floor is True
    assert "INSIDE the noise floor" in format_replicated_report(r)


def test_a_delta_clearing_the_floor_is_not_flagged() -> None:
    base = ReplicatedArm("base", [[False, False]] * 4)
    treat = ReplicatedArm("treat", [[True, True]] * 3 + [[False, False]])
    r = compare_replicated(base, treat)
    assert r.noise_floor == 0.0 and r.inside_noise_floor is False
    assert "outside the noise floor" in format_replicated_report(r)


# --- the seeds rule is in the report, not in a memory file --------------------------------------


def test_the_seeds_rule_is_said_out_loud() -> None:
    assert "sample" in seeds_verdict(1)
    assert "alert" in seeds_verdict(2)
    assert "decides" in seeds_verdict(3) and "decides" in seeds_verdict(10)


def test_the_report_carries_every_denominator() -> None:
    r = compare_replicated(ReplicatedArm("base", P5_P6), ReplicatedArm("chimera", P5_P6))
    text = format_replicated_report(r)
    assert "pass^2" in text and "12.5%" in text and "0.0%" in text
    assert "flip" in text and "25%" in text
    assert "ICC(1)" in text and "-0.08" in text
    assert "k=2" in text and "alert" in text


def test_summary_round_trips_the_numbers_and_keeps_none_as_none() -> None:
    arm = ReplicatedArm("a", [[True, False]], active=[[False, False]])
    s = arm.summary()
    assert s["icc"] is None and s["active_trials"] == 0 and s["active_pass_rate"] is None
    r = compare_replicated(ReplicatedArm("b", [[True, False]]), arm).summary()
    assert r["k"] == 2 and "alert" in str(r["seeds"])
