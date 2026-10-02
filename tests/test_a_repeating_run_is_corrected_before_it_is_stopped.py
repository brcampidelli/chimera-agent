"""A run that starts repeating is asked to change approach before anything stops it.

The owner decided on 2026-09-27 that the loop breaker is a warning plus a correction attempt, with a
high ceiling kept only as a net. `AgentConfig.loop_correction` is that behaviour, off by default so
every bench keeps the breaker it was measured with. The cases that matter are the ones a careless
version breaks: the correction landing between a tool call and its reply (a malformed transcript
that a provider rejects), the net not being a net (a spinning run that never ends), and the default
run changing at all.
"""

from __future__ import annotations

from typing import Any

from chimera.core.agent import Agent, AgentConfig
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

CORRECTION_MARK = "Change approach"


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


class _Stuck:
    """Calls the same tool forever, and keeps a copy of every request it was sent."""

    def __init__(self, *, gives_in_when_corrected: bool = False) -> None:
        self.calls = 0
        self.requests: list[list[Any]] = []
        self.gives_in = gives_in_when_corrected

    def complete(self, messages: Any, *, model: Any = None, temperature: float = 0.2, tools: Any = None) -> CompletionResult:
        self.calls += 1
        self.requests.append([dict(m) for m in messages])
        if tools is None:
            return CompletionResult(content="final answer", model="fake", tool_calls=[])
        corrected = any(
            m.get("role") == "user" and CORRECTION_MARK in str(m.get("content", "")) for m in messages
        )
        if self.gives_in and corrected:
            return CompletionResult(content="switched approach, done", model="fake", tool_calls=[])
        return CompletionResult(
            content="", model="fake",
            tool_calls=[ToolCall(id=f"c{self.calls}", name="spin", arguments={"x": 1})],
        )


def _run(backend: _Stuck, **config: Any) -> Any:
    agent = Agent(backend, _registry(), AgentConfig(max_steps=60, **config))
    return agent.run("go")


def _corrections(backend: _Stuck) -> int:
    last = backend.requests[-1]
    return sum(
        1 for m in last if m.get("role") == "user" and CORRECTION_MARK in str(m.get("content", ""))
    )


def test_the_default_run_is_the_breaker_every_bench_was_measured_with() -> None:
    backend = _Stuck()
    result = _run(backend)

    assert result.stopped_reason == "tool_loop"
    assert result.steps == 4  # four unchanged answers to the same call, as it always was
    assert _corrections(backend) == 0


def test_a_correcting_run_is_asked_to_change_approach_and_can_finish_instead_of_being_cut() -> None:
    backend = _Stuck(gives_in_when_corrected=True)
    result = _run(backend, loop_correction=True)

    assert result.stopped_reason == "final"  # the default would have stopped it at step 4
    assert result.answer == "switched approach, done"
    assert _corrections(backend) == 1


def test_a_run_that_ignores_the_correction_is_still_stopped_by_the_net() -> None:
    backend = _Stuck()
    result = _run(backend, loop_correction=True)

    assert result.stopped_reason == "tool_loop"
    assert result.steps == 8  # the wider net: eight unchanged answers, not four
    assert _corrections(backend) == 1  # said once, not on every repeat


def test_the_correction_never_lands_between_a_tool_call_and_its_reply() -> None:
    backend = _Stuck(gives_in_when_corrected=True)
    _run(backend, loop_correction=True)

    request = next(r for r in backend.requests if any(CORRECTION_MARK in str(m.get("content", "")) for m in r))
    at = next(i for i, m in enumerate(request) if CORRECTION_MARK in str(m.get("content", "")))
    assert request[at]["role"] == "user"
    assert request[at - 1]["role"] == "tool"  # right after the last reply of its step
    announced = {c["id"] for m in request[:at] if m.get("tool_calls") for c in m["tool_calls"]}
    answered = {m["tool_call_id"] for m in request[:at] if m.get("role") == "tool"}
    assert announced == answered


def test_the_warning_is_said_once_per_tool_however_long_the_run_keeps_repeating() -> None:
    heard: list[str] = []
    backend = _Stuck()
    agent = Agent(backend, _registry(), AgentConfig(max_steps=60, loop_correction=True))
    agent.run("go", on_notice=lambda code, text, data: heard.append(code))

    assert heard.count("tool_loop_warn") == 1


# --- who turns it on ---------------------------------------------------------------------------


def test_the_coding_route_asks_for_the_correction_and_a_spend_warning(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """A person is waiting on a desktop turn, so that is where the correction and the US$1 warning
    are on. Captured from the config the route hands the agent, not from the route's source."""
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.core.agent import AgentResult
    from chimera.core.context_budget import RunState
    from chimera.interface.session import ChatSession
    from chimera.orchestration.budget import DEFAULT_SPEND_WARN_USD

    seen: list[AgentConfig] = []

    class _Agent:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.run_state = RunState()
            seen.append(kwargs.get("config") or args[2])

        def run(self, task: str, **kw: Any) -> AgentResult:
            return AgentResult(
                answer="ok", steps=1, stopped_reason="final", transcript=[], tool_names=[], model="m"
            )

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(chimera.core, "Agent", _Agent, raising=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_Agent(None, None, AgentConfig())), workspace=ws, settings=settings)
    )
    seen.clear()

    client.post("/api/code/turn", json={"message": "go"})

    assert seen, "the route never built an agent"
    assert seen[-1].loop_correction is True
    assert seen[-1].warn_usd == DEFAULT_SPEND_WARN_USD
    assert seen[-1].max_usd is None  # and no ceiling nobody typed


def test_the_library_default_is_untouched() -> None:
    """Benches build `AgentConfig()` and their baselines were measured with it."""
    config = AgentConfig()

    assert config.loop_correction is False
    assert config.warn_usd is None
