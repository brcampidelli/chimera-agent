"""A provider connection that breaks mid-stream costs a resend of the step, not the whole turn.

Observed 2026-10-18 (study 34, item 7): OpenRouter closed a stream mid-answer — LiteLLM's
``MidStreamFallbackError`` wrapping "peer closed connection without sending complete message body
(incomplete chunked read)" — and the coding turn ended as "the coding turn failed" with nothing kept.
The gateway refuses to fall back once text was shown (rightly: it cannot splice two answers), and
the agent loop had no recovery of its own. Also here: context drift, which only reached the log
(item 10), is a notice on the turn.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core import Agent, AgentConfig
from chimera.core import agent as agent_module
from chimera.core.agent import GenerationInterrupted
from chimera.providers import CompletionResult, ToolCall
from chimera.providers.failover import is_transient_network
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool


class MidStreamFallbackError(Exception):
    """Named like LiteLLM's, carrying the transport error the way it does."""

    def __init__(self, message: str, original_exception: Exception | None = None) -> None:
        super().__init__(message)
        self.original_exception = original_exception
        self.status_code = 503


class RemoteProtocolError(Exception):
    pass


def _drop() -> MidStreamFallbackError:
    return MidStreamFallbackError(
        "litellm.ServiceUnavailableError: OpenrouterException - Error: mid-stream failure",
        original_exception=RemoteProtocolError(
            "peer closed connection without sending complete message body "
            "(incomplete chunked read)"
        ),
    )


class _Flaky:
    """Streams; the first ``drops`` streamed calls print some text and then break."""

    def __init__(self, drops: int, *, tool_first: bool = False, batch_fails: bool = False) -> None:
        self.drops = drops
        self.tool_first = tool_first
        self.batch_fails = batch_fails
        self.streamed = 0
        self.batch = 0
        self.served_tool = False

    def stream_complete(
        self, messages: list[Any], *, tools: Any = None, on_delta: Any = None, **kwargs: Any
    ) -> CompletionResult:
        self.streamed += 1
        if on_delta is not None:
            on_delta("partial ")
        if self.streamed <= self.drops:
            raise _drop()
        return self._answer()

    def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> CompletionResult:
        self.batch += 1
        if self.batch_fails:
            raise _drop()
        return self._answer()

    def _answer(self) -> CompletionResult:
        if self.tool_first and not self.served_tool:
            self.served_tool = True
            return CompletionResult(
                content="", model="fake",
                tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})],
            )
        return CompletionResult(content="finished", model="fake", prompt_tokens=5)


class _CountingEcho(EchoTool):
    def __init__(self) -> None:
        super().__init__()
        self.runs = 0

    def run(self, **kwargs: Any) -> str:
        self.runs += 1
        return super().run(**kwargs)


@pytest.fixture(autouse=True)
def _instant(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []
    monkeypatch.setattr(agent_module, "_retry_sleep", waits.append)
    return waits


def _notices() -> tuple[list[tuple[str, str]], Any]:
    seen: list[tuple[str, str]] = []
    return seen, lambda code, text, data: seen.append((code, text))


def test_the_observed_error_reads_as_a_transient_drop() -> None:
    assert is_transient_network(_drop())
    assert not is_transient_network(ValueError("Invalid API key provided (401)"))


def test_a_drop_mid_stream_is_sent_again_and_the_turn_finishes(_instant: list[float]) -> None:
    backend = _Flaky(drops=1)
    seen, on_notice = _notices()
    result = Agent(backend, ToolRegistry(), AgentConfig(max_steps=4)).run(
        "go", on_token=lambda _t: None, on_notice=on_notice
    )
    assert result.answer == "finished"
    assert backend.streamed == 2 and backend.batch == 0
    assert _instant == [1.0]
    # Said on the turn: the text the reader saw before the drop will be shown again.
    assert [code for code, _ in seen] == ["provider_retry"]
    assert "may repeat" in seen[0][1]


def test_a_resent_step_never_runs_a_finished_tool_twice() -> None:
    echo = _CountingEcho()
    registry = ToolRegistry()
    registry.register(echo)
    # Step 1 streams a tool call; step 2 breaks once, then answers.
    backend = _Flaky(drops=0, tool_first=True)
    original = backend.stream_complete
    broke = {"done": False}

    def second_step_breaks_once(messages: list[Any], **kwargs: Any) -> CompletionResult:
        if backend.served_tool and not broke["done"]:
            broke["done"] = True
            raise _drop()
        return original(messages, **kwargs)

    backend.stream_complete = second_step_breaks_once  # type: ignore[method-assign]
    result = Agent(backend, registry, AgentConfig(max_steps=6)).run("go", on_token=lambda _t: None)
    assert result.answer == "finished"
    assert echo.runs == 1


def test_after_the_resends_the_step_goes_through_the_fallback_chain(_instant: list[float]) -> None:
    backend = _Flaky(drops=99)
    seen, on_notice = _notices()
    result = Agent(backend, ToolRegistry(), AgentConfig(max_steps=4)).run(
        "go", on_token=lambda _t: None, on_notice=on_notice
    )
    assert result.answer == "finished"
    assert backend.streamed == 3 and backend.batch == 1
    assert _instant == [1.0, 4.0]
    assert [code for code, _ in seen][-1] == "provider_fallback"


def test_only_then_does_the_turn_fail_and_it_says_why() -> None:
    backend = _Flaky(drops=99, batch_fails=True)
    with pytest.raises(GenerationInterrupted) as caught:
        Agent(backend, ToolRegistry(), AgentConfig(max_steps=4)).run("go", on_token=lambda _t: None)
    message = str(caught.value)
    assert message.startswith("connection dropped")
    assert "MidStreamFallbackError" in message and "fallback" in message
    assert isinstance(caught.value, ConnectionError)


def test_a_refusal_is_not_resent() -> None:
    class _Refuses:
        calls = 0

        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            self.calls += 1
            raise PermissionError("Invalid API key (401)")

    backend = _Refuses()
    with pytest.raises(PermissionError):
        Agent(backend, ToolRegistry(), AgentConfig(max_steps=2)).run("go")
    assert backend.calls == 1


def test_context_drift_is_a_notice_on_the_turn() -> None:
    class _Circling:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, messages: list[Any], *, tools: Any = None, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls > 20:
                return CompletionResult(content="done", model="fake", prompt_tokens=100)
            text = f"item {self.calls % 3}" if self.calls > 8 else f"item {self.calls}"
            return CompletionResult(
                content="", model="fake", prompt_tokens=100 * self.calls,
                tool_calls=[ToolCall(id=f"c{self.calls}", name="echo", arguments={"text": text})],
            )

    registry = ToolRegistry()
    registry.register(EchoTool())
    seen, on_notice = _notices()
    result = Agent(_Circling(), registry, AgentConfig(max_steps=24)).run("go", on_notice=on_notice)
    assert result.steplog.drift.drifting is True
    drift = [text for code, text in seen if code == "context_drift"]
    assert len(drift) == 1 and drift[0].startswith("context drift at step ")
