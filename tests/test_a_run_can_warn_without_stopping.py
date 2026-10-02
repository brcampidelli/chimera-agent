"""A run can tell its caller something without changing what it does.

Every limit in the loop used to be one of two things: silent, or a stop. The `notice` channel is
the third, and its whole contract is that it is only ever a report: the same run, with or without
a listener, ends the same way, and a listener that fails cannot fail the run.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry


class _StuckBackend:
    """Makes the same tool call every step: a loop without the breaker."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: Any, *, model: Any = None, temperature: float = 0.2, tools: Any = None) -> CompletionResult:
        self.calls += 1
        if tools is None:  # the closing call, once the loop has ended
            return CompletionResult(content="final answer", model="fake", tool_calls=[])
        return CompletionResult(
            content="",
            model="fake",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="spin", arguments={"x": 1})],
        )


class _SpinTool(Tool):
    name = "spin"
    description = "does nothing new"
    parameters: dict[str, object] = {}

    def run(self, **kwargs: object) -> str:
        return "same output"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_SpinTool())
    return reg


def _collect() -> tuple[list[tuple[str, str, dict[str, Any]]], Any]:
    seen: list[tuple[str, str, dict[str, Any]]] = []

    def on_notice(code: str, text: str, data: dict[str, Any]) -> None:
        seen.append((code, text, data))

    return seen, on_notice


def test_the_loop_breaker_warns_before_it_breaks_and_still_breaks() -> None:
    seen, on_notice = _collect()
    agent = Agent(_StuckBackend(), _registry(), AgentConfig(max_steps=50, detect_tool_loops=True))
    result = agent.run("do something", on_notice=on_notice)

    warned = [s for s in seen if s[0] == "tool_loop_warn"]
    assert warned, "the third identical call is a warning, and nobody was told"
    assert warned[0][2]["tool"] == "spin"
    assert result.stopped_reason == "tool_loop"  # the warning did not replace the stop


def test_steps_low_is_said_once_and_the_run_still_ends_on_its_limit() -> None:
    seen, on_notice = _collect()
    agent = Agent(_StuckBackend(), _registry(), AgentConfig(max_steps=8, detect_tool_loops=False))
    result = agent.run("do something", on_notice=on_notice)

    assert [s[0] for s in seen].count("steps_low") == 1
    assert result.stopped_reason == "max_steps"
    assert result.steps == 8


def test_a_short_run_is_not_told_it_is_running_out() -> None:
    seen, on_notice = _collect()
    agent = Agent(_StuckBackend(), _registry(), AgentConfig(max_steps=4, detect_tool_loops=False))
    agent.run("do something", on_notice=on_notice)

    assert "steps_low" not in [s[0] for s in seen]


def test_a_listener_that_raises_cannot_change_how_the_run_ends() -> None:
    def broken(code: str, text: str, data: dict[str, Any]) -> None:
        raise RuntimeError("the UI is gone")

    with_listener = Agent(_StuckBackend(), _registry(), AgentConfig(max_steps=8, detect_tool_loops=False))
    without = Agent(_StuckBackend(), _registry(), AgentConfig(max_steps=8, detect_tool_loops=False))

    a = with_listener.run("do something", on_notice=broken)
    b = without.run("do something")

    assert (a.stopped_reason, a.steps, a.answer) == (b.stopped_reason, b.steps, b.answer)


def test_a_run_without_a_listener_is_unchanged() -> None:
    agent = Agent(_StuckBackend(), _registry(), AgentConfig(max_steps=50, detect_tool_loops=True))
    result = agent.run("do something")

    assert result.stopped_reason == "tool_loop"
    assert result.answer == "final answer"
