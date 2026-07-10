"""M19-A6: auto-rollback — retract the most recent artifact only on a SIGNIFICANT regression."""

from __future__ import annotations

from pathlib import Path

from chimera.eval.continuous import EvolutionReport, TaskOutcome
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.rollback import apply_rollback, assess_rollback
from chimera.evolution.skill_store import SkillStore


def _report(first_pass: int, first_fail: int, second_pass: int, second_fail: int,
            *, costs: tuple[int, ...] | None = None) -> EvolutionReport:
    outcomes: list[TaskOutcome] = []
    seq = [True] * first_pass + [False] * first_fail + [True] * second_pass + [False] * second_fail
    for i, passed in enumerate(seq):
        cost = costs[i] if costs else None
        outcomes.append(TaskOutcome(id=f"t{i}", passed=passed, cost=cost))
    return EvolutionReport(outcomes=outcomes)


def test_rolls_back_on_significant_degradation() -> None:
    # First half all pass, second half all fail, n=8 each -> CI lower bound > 0 -> significant.
    report = _report(8, 0, 0, 8)
    assert report.degraded_significantly() is True
    decision = assess_rollback(report, recent_artifacts=["old", "new"])
    assert decision.should_rollback
    assert decision.target == "new"  # newest-last -> the most recent artifact is retracted


def test_no_rollback_on_point_noise_small_sample() -> None:
    # A 3v3 split can look degraded pointwise but the CI can't speak -> never roll back.
    report = _report(3, 0, 1, 2)
    assert report.degraded_significantly() is None
    decision = assess_rollback(report, recent_artifacts=["old", "new"])
    assert not decision.should_rollback
    assert decision.target is None


def test_no_rollback_when_healthy() -> None:
    report = _report(8, 0, 8, 0)  # no degradation
    decision = assess_rollback(report, recent_artifacts=["new"])
    assert not decision.should_rollback


def test_rolls_back_on_cost_drift_even_without_accuracy_loss() -> None:
    # Accuracy holds (all pass) but the second half's mean cost inflates past tolerance.
    costs = (100,) * 8 + (500,) * 8
    report = _report(8, 0, 8, 0, costs=costs)
    assert report.degraded_significantly() is False
    decision = assess_rollback(report, recent_artifacts=["new"], cost_drift_tol=200.0)
    assert decision.should_rollback
    assert "cost drift" in decision.reason


def test_apply_rollback_retires_target_reversibly(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(LearnedSkill(name="old", description="d"))
    store.add(LearnedSkill(name="new", description="d"))
    report = _report(8, 0, 0, 8)
    decision = assess_rollback(report, recent_artifacts=["old", "new"])

    assert apply_rollback(store, decision) is True
    assert store.get("new").status == "retired"  # retired, not deleted
    assert store.get("old").status == "active"  # only the most recent one

    # reversible: approve un-retires it
    assert store.approve("new") is True
    assert store.get("new").status == "active"


def test_apply_rollback_noop_when_healthy(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(LearnedSkill(name="new", description="d"))
    decision = assess_rollback(_report(8, 0, 8, 0), recent_artifacts=["new"])
    assert apply_rollback(store, decision) is False
    assert store.get("new").status == "active"
