"""Tests for the self-optimizable agent spec + meta-search (OpenJarvis)."""

from __future__ import annotations

from chimera.ecosystem import AgentSpec, search_spec


def test_spec_dict_roundtrip() -> None:
    spec = AgentSpec(model="m", system_prompt="be terse", max_steps=10, fusion_panel=["a", "b"], memory_k=4)
    assert AgentSpec.from_dict(spec.to_dict()) == spec
    # unknown keys are ignored
    assert AgentSpec.from_dict({"model": "x", "junk": 1}).model == "x"


def _bump(spec: AgentSpec, by: int) -> AgentSpec:
    return AgentSpec.from_dict({**spec.to_dict(), "max_steps": spec.max_steps + by})


def test_search_keeps_improving_specs() -> None:
    def scorer(spec: AgentSpec) -> float:
        return spec.max_steps / 10.0  # higher steps score higher

    def proposer(spec: AgentSpec, score: float) -> AgentSpec:
        return _bump(spec, 1)

    result = search_spec(AgentSpec(max_steps=5), scorer, proposer, rounds=3)
    assert result.best.max_steps == 8  # 5 -> 6 -> 7 -> 8
    assert abs(result.best_score - 0.8) < 1e-9
    assert all(step.accepted for step in result.history)


def test_search_rejects_regression() -> None:
    def scorer(spec: AgentSpec) -> float:
        return 1.0 - spec.max_steps / 100.0  # higher steps score LOWER

    def proposer(spec: AgentSpec, score: float) -> AgentSpec:
        return _bump(spec, 10)

    result = search_spec(AgentSpec(max_steps=5), scorer, proposer, rounds=2)
    assert result.best.max_steps == 5  # initial kept; every candidate regressed
    assert result.history[1].accepted is False


def test_search_zero_rounds_returns_initial() -> None:
    result = search_spec(AgentSpec(max_steps=7), lambda s: 1.0, lambda s, sc: s, rounds=0)
    assert result.best.max_steps == 7
    assert len(result.history) == 1
