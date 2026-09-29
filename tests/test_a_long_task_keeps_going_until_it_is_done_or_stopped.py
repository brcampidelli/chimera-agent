"""`max_steps` is a window, not a wall, where a person is waiting.

The owner decided on 2026-09-27 that the step limit stops being a reason for the agent to stop:
auto-continue is on and has no total ceiling. What ends a run then is the work being done, a cancel,
the loop breaker's net, a ceiling the person typed, or a full context. This file holds both halves:
that the run really goes on, and that each of those stops still works, because an uncapped loop
whose only exits are untested is the version of this feature that burns a night's budget.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.core.agent import Agent, AgentConfig
from chimera.fusion.receipts import ModelPrice, set_price
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

PRICED = "openrouter/deepseek/deepseek-chat"


@pytest.fixture(autouse=True)
def _pinned_price() -> None:
    set_price(PRICED, ModelPrice(input_per_m=100.0, output_per_m=100.0))  # $0.20 for 1k in + 1k out


class _Work(Tool):
    """Real work: every call takes different arguments, so the loop breaker has nothing to see."""

    name = "work"
    description = "does a piece of work"
    parameters: dict[str, object] = {}

    def run(self, **kwargs: object) -> str:
        return f"done {kwargs.get('n')}"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_Work())
    return reg


class _Backend:
    """Works for `finish_after` steps, each on new arguments, then answers."""

    def __init__(self, finish_after: int | None, model: str = PRICED) -> None:
        self.calls = 0
        self.finish_after = finish_after
        self.model = model

    def complete(self, messages: Any, *, model: Any = None, temperature: float = 0.2, tools: Any = None) -> CompletionResult:
        self.calls += 1
        if tools is None or (self.finish_after is not None and self.calls > self.finish_after):
            return CompletionResult(
                content="all done", model=self.model, tool_calls=[],
                prompt_tokens=1000, completion_tokens=1000,
            )
        return CompletionResult(
            content="", model=self.model,
            tool_calls=[ToolCall(id=f"c{self.calls}", name="work", arguments={"n": self.calls})],
            prompt_tokens=1000, completion_tokens=1000,
        )


def _run(backend: Any, *, on_notice: Any = None, should_stop: Any = None, **config: Any) -> Any:
    cfg = AgentConfig(model=PRICED, max_steps=8, **config)
    return Agent(backend, _registry(), cfg).run("go", on_notice=on_notice, should_stop=should_stop)


def test_the_default_is_still_a_wall() -> None:
    backend = _Backend(finish_after=None)
    result = _run(backend)

    assert result.stopped_reason == "max_steps"
    assert result.steps == 8


def test_with_auto_continue_a_task_longer_than_the_window_finishes() -> None:
    heard: list[tuple[str, dict[str, Any]]] = []
    backend = _Backend(finish_after=25)
    result = _run(backend, auto_continue=True, on_notice=lambda c, t, d: heard.append((c, d)))

    assert result.stopped_reason == "final"
    assert result.answer == "all done"
    assert result.steps == 26
    extended = [d["steps"] for c, d in heard if c == "steps_extended"]
    assert extended == [8, 16, 24]  # said at the end of each window, and only then


def test_a_run_going_on_does_not_also_say_it_is_about_to_stop() -> None:
    heard: list[str] = []
    _run(_Backend(finish_after=20), auto_continue=True, on_notice=lambda c, t, d: heard.append(c))

    assert "steps_low" not in heard


def test_a_cancel_still_ends_an_uncapped_run() -> None:
    backend = _Backend(finish_after=None)
    result = _run(backend, auto_continue=True, should_stop=lambda: backend.calls >= 30)

    assert result.stopped_reason == "cancelled"
    assert backend.calls in (30, 31)


def test_a_ceiling_the_person_typed_still_ends_an_uncapped_run() -> None:
    backend = _Backend(finish_after=None)
    result = _run(backend, auto_continue=True, max_usd=1.0)

    assert result.stopped_reason == "spend"
    assert backend.calls < 10  # $0.20 a call: five calls reach $1.00


def test_the_loop_breakers_net_still_ends_a_run_that_only_spins() -> None:
    class _Spin(_Backend):
        def complete(self, messages: Any, **kw: Any) -> CompletionResult:
            self.calls += 1
            if kw.get("tools") is None:
                return CompletionResult(content="gave up", model=self.model, tool_calls=[])
            return CompletionResult(
                content="", model=self.model,
                tool_calls=[ToolCall(id=f"c{self.calls}", name="work", arguments={"n": 1})],
            )

    backend = _Spin(finish_after=None)
    result = _run(backend, auto_continue=True, loop_correction=True)

    assert result.stopped_reason == "tool_loop"
    assert result.steps < 30  # the net is a few steps wide, nowhere near forever


def test_the_library_default_is_untouched() -> None:
    assert AgentConfig().auto_continue is False


def test_the_coding_route_and_the_terminal_turn_auto_continue_on(tmp_path: Any, monkeypatch: Any) -> None:
    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.core.agent import AgentResult
    from chimera.core.context_budget import RunState
    from chimera.interface.session import ChatSession

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

    assert seen and seen[-1].auto_continue is True
