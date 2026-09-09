"""Tests for the spec-tuning scorer (OpenJarvis meta-search against scenarios)."""

from __future__ import annotations

from typing import Any

from chimera.core.agent import AgentResult
from chimera.ecosystem import AgentSpec, search_spec
from chimera.eval import Scenario, ScenarioTurn, SessionRequest, scenario_scorer
from chimera.interface import ChatSession


class _CannedAgent:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final")


def _builder(answer: str) -> Any:
    def build(_: SessionRequest) -> ChatSession:
        return ChatSession(_CannedAgent(answer))

    return build


_SCENARIOS = [
    Scenario(
        id="yes",
        turns=(ScenarioTurn("say yes"),),
        check=lambda ctx: "yes" in ctx.answers[-1].lower(),
        asserts="the answer says yes",
    ),
    Scenario(
        id="num",
        turns=(ScenarioTurn("the number"),),
        check=lambda ctx: "42" in ctx.answers[-1],
        asserts="the answer carries 42",
    ),
]


def test_scenario_scorer_returns_pass_rate() -> None:
    # spec.system_prompt is used as the canned answer here, so we can steer pass rate.
    score = scenario_scorer(lambda spec: _builder(spec.system_prompt), _SCENARIOS)
    assert score(AgentSpec(system_prompt="yes 42")) == 1.0  # both checks pass
    assert score(AgentSpec(system_prompt="yes")) == 0.5  # only the first passes
    assert score(AgentSpec(system_prompt="nope")) == 0.0


def test_search_spec_uses_scenario_scorer() -> None:
    scorer = scenario_scorer(lambda spec: _builder(spec.system_prompt), _SCENARIOS)

    def proposer(spec: AgentSpec, score: float) -> AgentSpec:
        return AgentSpec.from_dict({**spec.to_dict(), "system_prompt": "yes 42"})

    result = search_spec(AgentSpec(system_prompt="nope"), scorer, proposer, rounds=1)
    assert result.best_score == 1.0
    assert result.best.system_prompt == "yes 42"
