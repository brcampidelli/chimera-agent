"""Tests for the core ReAct agent loop using a scripted fake backend."""

from __future__ import annotations

from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


class ScriptedBackend:
    """Returns queued responses; once empty, answers a forced-final call."""

    def __init__(self, responses: list[CompletionResult], final: str = "final answer") -> None:
        self._responses = list(responses)
        self.final = final
        self.calls: list[dict[str, Any]] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.calls.append({"tools": tools, "messages": list(messages)})
        if self._responses:
            return self._responses.pop(0)
        return CompletionResult(content=self.final, model="fake")


def test_agent_runs_tool_then_finalizes() -> None:
    backend = ScriptedBackend(
        [
            CompletionResult(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "ping"})],
            ),
            CompletionResult(content="done", model="fake"),
        ]
    )
    agent = Agent(backend, _echo_registry())
    result = agent.run("say ping then finish")

    assert result.stopped_reason == "final"
    assert result.answer == "done"
    assert result.tool_calls_made == 1
    assert result.steps == 2
    # the tool observation "ping" is in the transcript
    assert any(m.get("content") == "ping" for m in result.transcript if isinstance(m, dict))


def test_agent_hits_max_steps_and_forces_answer() -> None:
    always_tool = CompletionResult(
        content="",
        model="fake",
        tool_calls=[ToolCall(id="c", name="echo", arguments={"text": "x"})],
    )

    class AlwaysToolBackend:
        def __init__(self) -> None:
            self.final_calls = 0

        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
            if tools is None:  # the forced-final call
                self.final_calls += 1
                return CompletionResult(content="forced", model="fake")
            return always_tool

    backend = AlwaysToolBackend()
    agent = Agent(backend, _echo_registry(), AgentConfig(max_steps=3))
    result = agent.run("loop forever")

    assert result.stopped_reason == "max_steps"
    assert result.answer == "forced"
    assert result.steps == 3
    assert backend.final_calls == 1


def test_agent_handles_unknown_tool_gracefully() -> None:
    backend = ScriptedBackend(
        [
            CompletionResult(
                content="",
                model="fake",
                tool_calls=[ToolCall(id="c1", name="does_not_exist", arguments={})],
            ),
            CompletionResult(content="recovered", model="fake"),
        ]
    )
    agent = Agent(backend, _echo_registry())
    result = agent.run("call a missing tool")

    assert result.answer == "recovered"
    assert any(
        isinstance(m, dict) and "unknown tool" in str(m.get("content", ""))
        for m in result.transcript
    )
