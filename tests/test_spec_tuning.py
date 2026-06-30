"""Tests for the spec-tuning scorer (OpenJarvis meta-search against scenarios)."""

from __future__ import annotations

from chimera.ecosystem import AgentSpec, search_spec
from chimera.eval import Scenario, scenario_scorer


class _Solver:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def solve(self, prompt: str) -> str:
        return self.answer


_SCENARIOS = [
    Scenario("yes", "say yes", lambda out: "yes" in out.lower()),
    Scenario("num", "the number", lambda out: "42" in out),
]


def test_scenario_scorer_returns_pass_rate() -> None:
    # spec.system_prompt is used as the canned answer here, so we can steer pass rate.
    score = scenario_scorer(lambda spec: _Solver(spec.system_prompt), _SCENARIOS)
    assert score(AgentSpec(system_prompt="yes 42")) == 1.0  # both checks pass
    assert score(AgentSpec(system_prompt="yes")) == 0.5  # only the first passes
    assert score(AgentSpec(system_prompt="nope")) == 0.0


def test_search_spec_uses_scenario_scorer() -> None:
    scorer = scenario_scorer(lambda spec: _Solver(spec.system_prompt), _SCENARIOS)

    def proposer(spec: AgentSpec, score: float) -> AgentSpec:
        return AgentSpec.from_dict({**spec.to_dict(), "system_prompt": "yes 42"})

    result = search_spec(AgentSpec(system_prompt="nope"), scorer, proposer, rounds=1)
    assert result.best_score == 1.0
    assert result.best.system_prompt == "yes 42"
