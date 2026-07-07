"""Tests for the tool-loop circuit breaker (M15-A4)."""

from __future__ import annotations

from chimera.core.agent import Agent, AgentConfig
from chimera.core.tool_loop import ToolLoopDetector
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools.base import Tool
from chimera.tools.registry import ToolRegistry

# --- the detector ------------------------------------------------------------------------


def test_identical_repeat_warns_then_breaks() -> None:
    det = ToolLoopDetector(repeat_warn=3, repeat_break=5)
    assert det.record("grep", {"q": "x"}).level == "ok"
    assert det.record("grep", {"q": "x"}).level == "ok"
    assert det.record("grep", {"q": "x"}).level == "warn"  # 3rd identical
    assert det.record("grep", {"q": "x"}).level == "warn"
    v = det.record("grep", {"q": "x"})  # 5th identical
    assert v.tripped and "identical args" in v.reason


def test_different_args_do_not_trip() -> None:
    det = ToolLoopDetector(repeat_break=3)
    # Same tool, genuinely different args each time — a real multi-step run, never trips.
    for i in range(8):
        assert det.record("read_file", {"path": f"f{i}.py"}).level == "ok"


def test_no_progress_polling_breaks() -> None:
    det = ToolLoopDetector(stall_break=4)
    for _ in range(3):
        assert det.record("poll", {"id": 1}, observation="pending").level != "break"
    v = det.record("poll", {"id": 1}, observation="pending")  # 4th unchanged output
    assert v.tripped and "unchanged output" in v.reason


def test_no_progress_needs_identical_observation() -> None:
    # Disable the identical-repeat detector (high thresholds) to isolate no-progress.
    det = ToolLoopDetector(stall_break=3, repeat_warn=99, repeat_break=99)
    # Same call signature but the output changes each time → progress, not a stall.
    assert det.record("poll", {"id": 1}, observation="a").tripped is False
    assert det.record("poll", {"id": 1}, observation="b").tripped is False
    assert det.record("poll", {"id": 1}, observation="c").tripped is False


def test_ping_pong_breaks() -> None:
    det = ToolLoopDetector(pingpong_cycles_break=3)
    seq = [("a", {"n": 1}), ("b", {"n": 2})] * 4  # A,B,A,B,A,B,A,B
    verdict = None
    for name, args in seq:
        verdict = det.record(name, args)
    assert verdict is not None and verdict.tripped
    assert "ping-pong" in verdict.reason


def test_empty_detector_is_ok() -> None:
    assert ToolLoopDetector()._assess().level == "ok"


# --- wired into the agent loop -----------------------------------------------------------


class _StuckBackend:
    """A backend that always makes the same tool call — an infinite loop without the breaker."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages, *, model=None, temperature=0.2, tools=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        if tools is None:  # the forced final-answer call after the breaker trips
            return CompletionResult(content="final answer with what I have", model="fake", tool_calls=[])
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


def test_agent_breaks_out_of_a_stuck_loop() -> None:
    backend = _StuckBackend()
    agent = Agent(backend, _registry(), AgentConfig(max_steps=50, detect_tool_loops=True))
    result = agent.run("do something")
    assert result.stopped_reason == "tool_loop"  # broke out, did not grind to 50 steps
    assert result.answer == "final answer with what I have"
    assert result.steps < 50


def test_breaker_can_be_disabled() -> None:
    backend = _StuckBackend()
    agent = Agent(backend, _registry(), AgentConfig(max_steps=6, detect_tool_loops=False))
    result = agent.run("do something")
    # No breaker → runs to max_steps and takes the budget-exhausted path.
    assert result.stopped_reason == "max_steps"
