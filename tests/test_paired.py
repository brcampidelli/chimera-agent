"""Tests for the paired A/B (M15-B1) and the checkpoint fork that makes it possible."""

from __future__ import annotations

from pathlib import Path

from chimera.core.runstate import RunCheckpointer
from chimera.eval.bench_ab import compare as compare_ab
from chimera.eval.paired import compare_paired, run_paired_experiment

# --- the paired statistic -----------------------------------------------------------------


def test_only_discordant_pairs_carry_signal() -> None:
    # 4 both-pass, 4 both-fail (concordant, no signal), 2 treatment-only wins.
    base = [True, True, True, True, False, False, False, False, False, False]
    treat = [True, True, True, True, False, False, False, False, True, True]
    r = compare_paired(base, treat)
    assert r.both_pass == 4 and r.both_fail == 4
    assert r.treatment_only == 2 and r.baseline_only == 0
    assert r.discordant == 2
    assert r.delta == 0.2  # (2 - 0) / 10


def test_paired_tightens_to_significance_where_unpaired_cannot() -> None:
    """The killer property: same data, paired excludes 0 while unpaired Newcombe does not."""
    # 8 both-pass, 8 both-fail, 4 treatment-only wins, 0 baseline-only.
    base = [True] * 8 + [False] * 8 + [False] * 4
    treat = [True] * 8 + [False] * 8 + [True] * 4
    assert len(base) == len(treat) == 20

    unpaired = compare_ab(base, treat)  # marginals 12/20 vs 8/20
    assert unpaired.significant is False  # Newcombe CI includes 0 at this n

    paired = compare_paired(base, treat)
    assert paired.significant is True  # conditioning on the 4 discordant pairs clears zero
    lo, _ = paired.diff_ci
    assert lo > 0


def test_no_discordant_pairs_is_not_significant() -> None:
    same = [True, False, True, False]
    r = compare_paired(same, list(same))
    assert r.discordant == 0
    assert r.diff_ci == (0.0, 0.0)
    assert r.significant is False


def test_baseline_wins_gives_negative_delta() -> None:
    base = [True, True, False, False]
    treat = [False, False, False, False]  # baseline won 2 pairs, treatment 0
    r = compare_paired(base, treat)
    assert r.delta == -0.5
    assert r.baseline_only == 2 and r.treatment_only == 0


def test_length_mismatch_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="same length"):
        compare_paired([True], [True, False])


def test_summary_and_empty() -> None:
    assert compare_paired([], []).n == 0
    s = compare_paired([True, False], [True, True]).summary()
    assert s["n"] == 2 and s["significant"] in (True, False)


# --- the replay harness (restore identical state before each arm) -------------------------


def test_run_paired_experiment_restores_before_each_arm() -> None:
    restores: list[str] = []

    def restore(item: str) -> None:
        restores.append(item)

    # baseline fails the "hard" items; treatment fixes them → treatment wins the discordant pairs.
    result = run_paired_experiment(
        ["easy", "hard", "hard", "easy"],
        restore=restore,
        baseline=lambda it: it == "easy",
        treatment=lambda _it: True,
    )
    # restore called twice per item (once per arm) → both arms start from the identical state.
    assert restores == ["easy", "easy", "hard", "hard", "hard", "hard", "easy", "easy"]
    assert result.treatment_only == 2 and result.baseline_only == 0


# --- the checkpoint fork that supplies the identical state ---------------------------------


def test_fork_branches_a_checkpoint(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    cp.save("main", {"task": "solve x", "next_index": 3})
    assert cp.fork("main", "arm-baseline") is True
    assert cp.fork("main", "arm-candidate") is True
    # Both arms hold the identical captured state.
    assert cp.load("arm-baseline") == {"task": "solve x", "next_index": 3}
    assert cp.load("arm-candidate") == {"task": "solve x", "next_index": 3}
    assert set(cp.threads()) == {"main", "arm-baseline", "arm-candidate"}


def test_fork_missing_source_returns_false(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    assert cp.fork("ghost", "dst") is False


def test_fork_refuses_to_clobber_without_overwrite(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    cp.save("src", {"v": 1})
    cp.save("dst", {"v": 2})
    assert cp.fork("src", "dst") is False  # would clobber
    assert cp.load("dst") == {"v": 2}  # untouched
    assert cp.fork("src", "dst", overwrite=True) is True
    assert cp.load("dst") == {"v": 1}  # now branched
