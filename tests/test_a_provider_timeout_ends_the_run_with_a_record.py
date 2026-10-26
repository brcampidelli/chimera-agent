"""A provider that does not answer in time ends the run with a record, not with a crash.

Measured 26/10/2026 (SWE-bench regression run, `chimera solve`, Haiku 5.5 through OpenRouter): the
primary call hit a 600 s read timeout, the fallback model timed out too, and the process died with
exit 1 and a traceback before writing its final JSON or its `runs.jsonl` line. The receipts read 0
tool calls, 0 attempts and no cost, although the calls already made had been paid for.

The timeout is not a transient drop, so `_call_model` re-raises it on purpose (no resend of a call
that already waited ten minutes). Nothing above the loop turned that re-raise into an ending, so the
exception escaped `Agent.run`, `AutonomousAgent.run` and `solve`. This file pins the ending: one
attempt, the money already spent kept on the record, and `stopped_reason` naming the timeout.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.api.runs import build_receipt
from chimera.cli.commands._shared import _exit_for
from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import Agent, AgentConfig
from chimera.providers import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool

MODEL = "openrouter/anthropic/claude-haiku-5.5"


class Timeout(Exception):
    """Named like LiteLLM's ``litellm.Timeout``, which is what the gateway raised in the run."""


def _answer(tool: str | None) -> CompletionResult:
    result = CompletionResult(
        content="working" if tool else "done",
        model=MODEL,
        prompt_tokens=3000,
        completion_tokens=500,
        tool_calls=[ToolCall(id="c1", name=tool, arguments={"text": "x"})] if tool else [],
    )
    return result


class _TimesOut:
    """The first call answers with a tool call, then every call times out: the primary, and
    the fallback behind it, which the gateway already tried before the error reached the loop."""

    def __init__(self, *, answer_first: bool) -> None:
        self.answer_first = answer_first
        self.calls = 0

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        if self.answer_first and self.calls == 1:
            return _answer("echo")
        raise Timeout("Timeout Error: OpenrouterException - Connection timed out after 600.0 seconds.")


def _agent(backend: _TimesOut) -> Agent:
    registry = ToolRegistry()
    registry.register(EchoTool())
    return Agent(backend, registry, AgentConfig(model=MODEL, max_steps=6))


def test_a_timed_out_run_returns_a_result_instead_of_raising() -> None:
    backend = _TimesOut(answer_first=True)

    result = _agent(backend).run("fix the bug")

    assert result.stopped_reason == "provider_timeout"
    assert "timed out" in result.answer


def test_the_calls_that_answered_before_the_timeout_are_kept() -> None:
    result = _agent(_TimesOut(answer_first=True)).run("fix the bug")

    assert result.prompt_tokens == 3000 and result.completion_tokens == 500
    assert result.tool_calls_made == 1, "the tool the first answer asked for ran before the timeout"


def test_a_timeout_on_the_first_call_records_nothing_spent() -> None:
    # The same rule as the cap: a run that never reached a model has nothing to bill, and an
    # invented zero-cost row would be indistinguishable from a real one.
    result = _agent(_TimesOut(answer_first=False)).run("fix the bug")

    assert result.stopped_reason == "provider_timeout"
    assert result.prompt_tokens == 0 and result.completion_tokens == 0


def test_the_timed_out_call_is_not_resent() -> None:
    backend = _TimesOut(answer_first=True)

    _agent(backend).run("fix the bug")

    assert backend.calls == 2, "one answered call and one timed-out call; no silent retry"


def test_the_autonomous_run_ends_as_provider_timeout_with_the_attempt_on_record() -> None:
    backend = _TimesOut(answer_first=True)
    cfg = AutonomousConfig(max_attempts=3, use_planner=False, use_manager=False)
    result = AutonomousAgent(_agent(backend), config=cfg).run("fix the bug")

    assert result.ending == "provider_timeout"
    assert result.stopped_reason == "provider_timeout"
    assert result.success is False
    assert len(result.attempts) == 1, "no retry of a worker whose provider is not answering"
    assert result.attempts[0].prompt_tokens == 3000, "the call that answered is billed on the attempt"


def test_the_receipt_names_the_timeout() -> None:
    cfg = AutonomousConfig(max_attempts=3, use_planner=False, use_manager=False)
    result = AutonomousAgent(_agent(_TimesOut(answer_first=True)), config=cfg).run("fix the bug")

    receipt = build_receipt(result, "fix the bug", None, "2026-10-26T00:00:00Z")

    assert receipt.ending == "provider_timeout" and receipt.stopped_reason == "provider_timeout"


def test_the_exit_code_is_a_declared_stop_not_a_crash() -> None:
    # 1 is the code a traceback leaves, and `unknown` (12) is the catch-all: a timeout has its own.
    assert _exit_for("provider_timeout") == 13


@pytest.mark.parametrize("message", ["", "connection refused"])
def test_only_a_timeout_takes_this_path(message: str) -> None:
    """The control: an error that is not a timeout keeps raising, as it always did."""

    class _Refused:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            raise PermissionError(message or "Invalid API key (401)")

    registry = ToolRegistry()
    with pytest.raises(PermissionError):
        Agent(_Refused(), registry, AgentConfig(model=MODEL, max_steps=3)).run("x")
