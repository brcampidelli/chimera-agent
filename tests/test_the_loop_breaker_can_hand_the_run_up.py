"""Study 24, M6: the tool-loop breaker may hand the run to a stronger model instead of stopping it.

Off by default. With `escalate_on_tool_loop` set, the FIRST trip switches the run's model, resets the
detector and continues with every tool; a trip on the stronger model stops exactly as a run without
escalation does. The switch is per run: the Agent's own config is never mutated for the next run.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry

WEAK, STRONG = "openrouter/openai/gpt-oss-20b", "openrouter/deepseek/deepseek-v3.2"


class _Result:
    def __init__(self, content: str, model: str) -> None:
        self.content = content
        self.model = model
        self.prompt_tokens = 100
        self.completion_tokens = 10
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.tool_calls: list[Any] = []
        self.finish_reason = "stop"
        self.route_meta: dict[str, Any] | None = None


class _Ping(Tool):
    name = "ping"
    description = "does nothing"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def run(self, **kwargs: Any) -> str:
        return "pong"


class _Backend:
    """Loops on `ping` forever on any model in `loops_on`; answers otherwise. Records each model asked."""

    def __init__(self, loops_on: set[str]) -> None:
        self.loops_on = loops_on
        self.asked: list[str] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        model = str(kwargs.get("model") or "")
        self.asked.append(model)
        result = _Result("done", model)
        if model in self.loops_on and kwargs.get("tools"):
            result.tool_calls = [ToolCall(id=f"c{len(self.asked)}", name="ping", arguments={})]
        return result


def _run(backend: _Backend, escalate: str | None) -> tuple[Any, AgentConfig]:
    registry = ToolRegistry()
    registry.register(_Ping())
    config = AgentConfig(model=WEAK, max_steps=30, escalate_on_tool_loop=escalate)
    return Agent(backend, registry, config).run("do the thing"), config


def test_without_escalation_the_breaker_stops_as_before() -> None:
    backend = _Backend(loops_on={WEAK})
    result, _ = _run(backend, None)
    assert result.stopped_reason == "tool_loop"
    assert set(backend.asked) == {WEAK}


def test_the_first_trip_hands_the_run_to_the_stronger_model() -> None:
    backend = _Backend(loops_on={WEAK})
    result, config = _run(backend, STRONG)
    assert result.stopped_reason == "final"
    switch = backend.asked.index(STRONG)
    assert set(backend.asked[:switch]) == {WEAK} and set(backend.asked[switch:]) == {STRONG}
    assert config.model == WEAK  # per run: the Agent's config is not mutated


def test_a_trip_on_the_stronger_model_still_stops_the_run() -> None:
    backend = _Backend(loops_on={WEAK, STRONG})
    result, _ = _run(backend, STRONG)
    assert result.stopped_reason == "tool_loop"
    assert backend.asked.count(STRONG) >= 2  # it ran on the stronger model, then was stopped there
    assert backend.asked[-1] == STRONG  # the final no-tools answer comes from the model the run ended on
