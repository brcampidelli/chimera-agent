"""The agent loop never makes a reasoning trace its answer — and says when the route filed one.

A route can file a reasoning model's whole reply as reasoning
(`CompletionResult.answer_in_reasoning`; `deepseek-r1` on Novita did it on 37–43% of calls,
`bench/review_judge/RESULTS-h11.md`). The loop expects prose, and prose is not recovered from a
thought trace: the trace would be shown to the person and can end on a draft. So the loop keeps the
re-ask of #619/#624 and, when both replies came back
empty, its note says the text was filed as reasoning instead of leaving "empty" unexplained. Every
ending that asks for a closing reply is covered: the natural ending, the step limit and the loop
breaker. Fakes only; the same path through the real gateway and LiteLLM's parser is in
`test_an_answer_filed_as_reasoning_is_flagged_not_promoted.py`.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core import Agent, AgentConfig
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool

THOUGHT = 'The fix is in utils.py; I edited it and the tests pass.\n{"summary": "fixed utils.py"}'
TOOL_TURN = CompletionResult(
    content="", model="fake", tool_calls=[ToolCall(id="c", name="echo", arguments={"text": "x"})]
)


def _filed() -> CompletionResult:
    return CompletionResult(content="", model="fake", finish_reason="stop", reasoning=THOUGHT,
                            answer_in_reasoning=True)


def _empty() -> CompletionResult:
    return CompletionResult(content="", model="fake", finish_reason="stop")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return registry


class _Script:
    """Calls a tool ``tool_turns`` times while it has tools, then answers from ``replies``; the
    closing calls, made without tools, answer from ``replies`` too."""

    def __init__(self, replies: list[CompletionResult], tool_turns: int) -> None:
        self.replies = list(replies)
        self.tool_turns = tool_turns

    def complete(
        self, messages: list[Any], *, tools: Any = None, **kwargs: Any
    ) -> CompletionResult:
        if tools is not None and self.tool_turns > 0:
            self.tool_turns -= 1
            return TOOL_TURN
        return self.replies.pop(0)


def _assert_no_reasoning(result: Any) -> None:
    assert "utils.py" not in result.answer and "summary" not in result.answer
    for message in result.transcript:
        if isinstance(message, dict):
            assert "utils.py" not in str(message.get("content"))


@pytest.mark.parametrize(
    ("tool_turns", "max_steps", "stopped"),
    [(1, 8, "final"), (99, 3, "max_steps"), (99, 20, "tool_loop")],
)
def test_two_replies_filed_as_reasoning_end_in_a_note_that_says_so(
    tool_turns: int, max_steps: int, stopped: str
) -> None:
    backend = _Script([_filed(), _filed()], tool_turns)
    result = Agent(backend, _registry(), AgentConfig(max_steps=max_steps)).run("fix utils")
    assert result.stopped_reason == stopped
    assert result.answer.startswith("(No final answer:")
    assert "filed the model's text as reasoning" in result.answer
    _assert_no_reasoning(result)


def test_one_filed_reply_and_one_empty_one_still_say_so() -> None:
    backend = _Script([_filed(), _empty()], 1)
    result = Agent(backend, _registry(), AgentConfig(max_steps=8)).run("fix utils")
    assert "filed the model's text as reasoning" in result.answer
    _assert_no_reasoning(result)


def test_a_real_empty_says_nothing_about_reasoning() -> None:
    backend = _Script([_empty(), _empty()], 1)
    result = Agent(backend, _registry(), AgentConfig(max_steps=8)).run("fix utils")
    assert result.answer.startswith("(No final answer:")
    assert "reasoning" not in result.answer


def test_a_filed_reply_then_a_written_one_answers_with_the_written_one() -> None:
    backend = _Script([_filed(), CompletionResult(content="Fixed utils.py.", model="fake")], 1)
    result = Agent(backend, _registry(), AgentConfig(max_steps=8)).run("fix utils")
    assert result.answer == "Fixed utils.py."
