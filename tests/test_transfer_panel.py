"""Proposing and testing a skill are different jobs, and must not share a budget.

Strong models propose; cheap models decide whether it transfers. Widening the *test* sample is what
makes the acceptance statistic mean anything — and it must not widen the fusion bill to do it.
"""

from __future__ import annotations

from chimera.config import _DEFAULT_TRANSFER_PANEL, Settings
from chimera.eval.anytime import best_possible_wilson
from chimera.evolution.collective import CollectiveSkillEvolver
from chimera.evolution.learned_skill import LearnedSkill
from chimera.skills.base import SkillResult


class _RecordingBackend:
    """Records which model each call went to."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def complete(self, *args: object, **kwargs: object) -> str:
        model = kwargs.get("model")
        self.calls.append(str(model))
        return "ok"


def _skill() -> LearnedSkill:
    return LearnedSkill(
        name="demo",
        description="d",
        prompt_template="do {task}",
        backend=_RecordingBackend(),
        model="proposer",
    )


def test_transfer_is_measured_on_the_transfer_panel_not_the_fusion_panel(monkeypatch) -> None:
    backend = _RecordingBackend()
    evolver = CollectiveSkillEvolver(
        backend,
        ["frontier-a", "frontier-b"],
        transfer_models=["cheap-1", "cheap-2", "cheap-3", "cheap-4"],
    )

    seen: list[str] = []

    def fake_execute(self: LearnedSkill, **_: object) -> SkillResult:
        seen.append(self.model)
        return SkillResult(ok=True, output="fine")

    monkeypatch.setattr(LearnedSkill, "execute", fake_execute)
    passed, n = evolver.transfer_counts(_skill(), {}, lambda out: True)

    assert (passed, n) == (4, 4)  # n is the transfer panel's size
    assert seen == ["cheap-1", "cheap-2", "cheap-3", "cheap-4"]
    assert "frontier-a" not in seen  # the expensive panel is never billed for testing


def test_transfer_panel_defaults_to_the_fusion_panel_when_unset() -> None:
    """Back-compatible: callers that pass only one panel keep the old behaviour exactly."""
    evolver = CollectiveSkillEvolver(_RecordingBackend(), ["a", "b", "c"])
    assert evolver.transfer_models == ["a", "b", "c"]


def test_the_shipped_transfer_panel_can_actually_satisfy_the_default_gate() -> None:
    """The whole point: n=3 made a 0.5 Wilson gate unreachable; the shipped panel must not."""
    n = len(_DEFAULT_TRANSFER_PANEL)
    assert n >= 6
    assert best_possible_wilson(n, k=3) > 0.5


def test_transfer_panel_is_configurable_and_separate_from_the_fusion_panel() -> None:
    settings = Settings(
        CHIMERA_FUSION_PANEL="a,b",
        CHIMERA_TRANSFER_PANEL="x,y,z",
    )
    assert settings.fusion_panel == ["a", "b"]
    assert settings.transfer_panel == ["x", "y", "z"]
