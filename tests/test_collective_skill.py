"""Tests for collective skill evolution across a model panel (no network)."""

from __future__ import annotations

from typing import Any

from chimera.evolution import CollectiveSkillEvolver, LearnedSkill
from chimera.providers import CompletionResult

PROPOSAL_A = '{"name": "skill_a", "description": "d", "prompt_template": "do {x}"}'
PROPOSAL_B = '{"name": "skill_b", "description": "d", "prompt_template": "do {x}"}'


class ModelBackend:
    """Returns content keyed by the model passed to complete()."""

    def __init__(self, by_model: dict[str, str]) -> None:
        self.by_model = by_model

    def complete(self, messages: list[Any], *, model: str | None = None, **_: Any) -> CompletionResult:
        return CompletionResult(content=self.by_model.get(model or "", ""), model=model or "x")


def test_propose_collective_dedups_by_name() -> None:
    cse = CollectiveSkillEvolver(ModelBackend({"m1": PROPOSAL_A, "m2": PROPOSAL_A}), ["m1", "m2"])
    assert len(cse.propose_collective("task", "sol")) == 1


def test_propose_collective_collects_distinct_candidates() -> None:
    cse = CollectiveSkillEvolver(ModelBackend({"m1": PROPOSAL_A, "m2": PROPOSAL_B}), ["m1", "m2"])
    assert {s.name for s in cse.propose_collective("task", "sol")} == {"skill_a", "skill_b"}


def test_transferability_is_fraction_of_passing_models() -> None:
    backend = ModelBackend({"m1": "real output", "m2": ""})  # m2 returns empty -> fails check
    skill = LearnedSkill(name="s", description="d", prompt_template="do {x}", backend=backend)
    cse = CollectiveSkillEvolver(backend, ["m1", "m2"])
    score = cse.transferability(skill, {"x": "thing"}, lambda out: bool(out.strip()))
    assert score == 0.5


def test_evolve_collective_keeps_best_transferable() -> None:
    cse = CollectiveSkillEvolver(ModelBackend({"m1": PROPOSAL_A, "m2": PROPOSAL_A}), ["m1", "m2"])
    out = cse.evolve_collective(
        "task", "sol", test_input={"x": "t"}, check=lambda o: bool(o.strip()), min_transfer=0.5
    )
    assert out is not None
    skill, score = out
    assert skill.name == "skill_a" and score == 1.0


class QueuedBackend:
    """Per-model FIFO queue: first call (propose) returns JSON, second (execute) output."""

    def __init__(self, by_model: dict[str, list[str]]) -> None:
        self.by_model = {m: list(v) for m, v in by_model.items()}

    def complete(self, messages: list[Any], *, model: str | None = None, **_: Any) -> CompletionResult:
        queue = self.by_model.get(model or "", [])
        return CompletionResult(content=queue.pop(0) if queue else "", model=model or "x")


def test_evolve_collective_rejects_below_transfer_threshold() -> None:
    # propose returns a valid skill, but it produces empty output on every model ->
    # transferability 0 < min_transfer -> rejected.
    backend = QueuedBackend({"m1": [PROPOSAL_A, ""], "m2": [PROPOSAL_A, ""]})
    cse = CollectiveSkillEvolver(backend, ["m1", "m2"])
    out = cse.evolve_collective(
        "task", "sol", test_input={"x": "t"}, check=lambda o: bool(o.strip()), min_transfer=0.5
    )
    assert out is None


def test_auto_evolver_uses_collective_path(tmp_path: Any) -> None:
    from chimera.evolution import AutoSkillEvolver, SkillEvolver, SkillStore

    backend = ModelBackend({"m1": PROPOSAL_A, "m2": PROPOSAL_A})
    store = SkillStore(tmp_path / "skills.json")
    auto = AutoSkillEvolver(
        SkillEvolver(backend),
        store,
        collective=CollectiveSkillEvolver(backend, ["m1", "m2"]),
    )
    kept = auto.maybe_evolve("task", "sol", prior_successes=2)
    assert kept is not None and kept.name == "skill_a"
    assert "skill_a" in store


def test_auto_evolver_collective_rejects_untransferable(tmp_path: Any) -> None:
    from chimera.evolution import AutoSkillEvolver, SkillEvolver, SkillStore

    # propose succeeds but the skill produces empty output on every model -> not kept
    backend = QueuedBackend({"m1": [PROPOSAL_A, ""], "m2": [PROPOSAL_A, ""]})
    store = SkillStore(tmp_path / "skills.json")
    auto = AutoSkillEvolver(
        SkillEvolver(backend),
        store,
        collective=CollectiveSkillEvolver(backend, ["m1", "m2"]),
    )
    assert auto.maybe_evolve("task", "sol", prior_successes=2) is None
    assert "skill_a" not in store
