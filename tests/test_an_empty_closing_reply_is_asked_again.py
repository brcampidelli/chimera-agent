"""A run that reaches its step limit (or trips the loop breaker) closes with one more call, without
tools, for the final answer. On the product default that call came back EMPTY in 6 of 10 unattended
solves that reached `max_steps` (`bench/unattended_claims`), and four of the six had done the work;
the owner read a blank result. A live probe found no dropped tool call behind it — `finish_reason`
"stop", a few hundred completion tokens, empty content: the model reasoned its answer and wrote none.

So an empty closing reply is asked once more, and a second empty one is reported as what it is
instead of as a blank answer. A closing reply with text costs nothing extra. Fakes only."""

from __future__ import annotations

import re
from typing import Any

from chimera.core import Agent, AgentConfig
from chimera.core.agent import _EMPTY_CLOSE_NUDGE
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool

TOOL_TURN = CompletionResult(
    content="", model="fake", tool_calls=[ToolCall(id="c", name="echo", arguments={"text": "x"})]
)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


class _Closing:
    """Always calls a tool while it has tools; answers the closing calls from a script."""

    def __init__(self, closings: list[str]) -> None:
        self.closings = list(closings)
        self.closing_prompts: list[str] = []

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        if tools is not None:
            return TOOL_TURN
        self.closing_prompts.append(str(messages[-1]["content"]))
        return CompletionResult(content=self.closings.pop(0), model="fake")


def test_an_empty_closing_reply_is_asked_once_more() -> None:
    backend = _Closing(["", "Changed x; ran the tests: 4 passed."])
    result = Agent(backend, _registry(), AgentConfig(max_steps=3)).run("loop")
    assert result.stopped_reason == "max_steps"
    assert result.answer == "Changed x; ran the tests: 4 passed."
    assert len(backend.closing_prompts) == 2
    assert backend.closing_prompts[1].endswith(_EMPTY_CLOSE_NUDGE)
    assert result.transcript[-1] == {"role": "assistant", "content": result.answer}


def test_a_closing_reply_with_text_costs_no_extra_call() -> None:
    backend = _Closing(["done", "never asked"])
    result = Agent(backend, _registry(), AgentConfig(max_steps=3)).run("loop")
    assert result.answer == "done"
    assert len(backend.closing_prompts) == 1


def test_two_empty_closing_replies_say_so_and_claim_nothing() -> None:
    backend = _Closing(["", "   "])
    result = Agent(backend, _registry(), AgentConfig(max_steps=3)).run("loop")
    assert result.stopped_reason == "max_steps"
    assert result.answer.startswith("(No final answer:")
    assert "echo ×3" in result.answer
    words = set(re.findall(r"[a-z]+", result.answer.lower()))
    assert not words & {"passed", "passes", "works", "done", "fixed", "success", "succeeded"}


def test_the_loop_breaker_closing_is_asked_again_too() -> None:
    backend = _Closing(["", "Stopped repeating echo; nothing else to do."])
    result = Agent(backend, _registry(), AgentConfig(max_steps=20)).run("loop")
    assert result.stopped_reason == "tool_loop"
    assert result.answer == "Stopped repeating echo; nothing else to do."
    assert len(backend.closing_prompts) == 2
