"""A turn that fails after paying for work still has to say what it paid.

Measured on rc13: a coding turn made seven tool calls, wrote 14.7 KB of HTML and 4.7 KB of CSS,
then died. No `done` frame, no session, and **nothing in the usage log** — the tokens behind
nineteen kilobytes of correct output left no trace at all.

The mechanism is small and total: `_log_usage` is called from `_verify_and_finish`, which only the
success path reaches. Everything a run had already spent lives in a tally local to `Agent.run`, and
an exception takes the frame with it.

The distinction that keeps this honest: a turn that fails *before* spending anything must still
record nothing. Otherwise the fix trades a silent undercount for a fabricated row.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig, partial_spend
from chimera.providers.gateway import ToolCall
from chimera.tools.registry import Tool, ToolRegistry


class _Result:
    def __init__(self, content: str, model: str) -> None:
        self.content = content
        self.model = model
        self.prompt_tokens = 3000
        self.completion_tokens = 500
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


class _DiesAfter:
    """Answers ``ok_calls`` times, then raises the way a provider does."""

    MODEL = "openrouter/deepseek/deepseek-chat"

    def __init__(self, ok_calls: int) -> None:
        self.ok_calls = ok_calls
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> _Result:
        self.calls += 1
        if self.calls > self.ok_calls:
            raise RuntimeError("upstream said no")
        result = _Result(f"answer {self.calls}", self.MODEL)
        result.tool_calls = [ToolCall(id=f"c{self.calls}", name="ping", arguments={})]
        return result


def _agent(ok_calls: int) -> Agent:
    registry = ToolRegistry()
    registry.register(_Ping())
    return Agent(_DiesAfter(ok_calls), registry, AgentConfig(model="", max_steps=6))


def test_the_failure_carries_what_was_already_paid_for() -> None:
    with pytest.raises(RuntimeError) as caught:
        _agent(ok_calls=2).run("do the thing")

    spent = partial_spend(caught.value)

    assert spent is not None, "the exception said nothing about the two calls that were paid for"
    assert spent.prompt_tokens == 6000
    assert spent.completion_tokens == 1000
    assert spent.steps == 2
    # The name matters as much as the number: a row with dollars and no model is the other half of
    # this same complaint, fixed alongside it.
    assert spent.model == _DiesAfter.MODEL
    assert spent.usd and spent.usd > 0


def test_a_turn_that_never_reached_the_model_records_nothing() -> None:
    # The rc13 test that produced this: a nonexistent model name fails on the first call, and there
    # the empty usage log is CORRECT. A fix that logged a zero row here would replace an
    # undercount with an invention, which is worse — an invented row cannot be told from a real one.
    with pytest.raises(RuntimeError) as caught:
        _agent(ok_calls=0).run("do the thing")

    spent = partial_spend(caught.value)

    assert spent is None or (spent.prompt_tokens == 0 and spent.completion_tokens == 0)


def test_a_successful_run_attaches_nothing_anywhere() -> None:
    # There is no exception to read, and the success path has always accounted for itself. Stated
    # so the mechanism cannot quietly grow into a second accounting path beside `_log_usage`.
    result = _agent(ok_calls=99).run("do the thing")

    assert result.stopped_reason in {"final", "max_steps", "tool_loop"}
    assert result.prompt_tokens > 0
