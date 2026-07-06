"""Tests for streaming (M13 C1) — token primitive + structured run events."""

from __future__ import annotations

from types import SimpleNamespace

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.events import AgentEvent, attempt, final, result, status
from chimera.providers.gateway import _delta_text

# --- token primitive: chunk extraction --------------------------------------------------


def _chunk(content: str | None) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def test_delta_text_extracts_content() -> None:
    assert _delta_text(_chunk("hello")) == "hello"


def test_delta_text_handles_empty_and_malformed() -> None:
    assert _delta_text(_chunk(None)) == ""
    assert _delta_text(SimpleNamespace(choices=[])) == ""  # no crash on odd shapes
    assert _delta_text(object()) == ""


# --- event constructors -----------------------------------------------------------------


def test_event_constructors_shape() -> None:
    assert status("go").kind == "status"
    assert attempt(2, 5).data == {"index": 2, "max_attempts": 5}
    assert result(1, success=False, detail="x").data["success"] is False
    assert final(True, "answer").data == {"success": True, "answer": "answer"}


# --- run emits a coherent event stream --------------------------------------------------


class _OkWorker:
    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="done", steps=1, transcript=[], stopped_reason="done")


class _FailWorker:
    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="nope", steps=1, transcript=[], stopped_reason="done")


class _RejectManager:
    def review(self, task: str, answer: str, context: str) -> object:
        from chimera.core.supervisor import Review

        return Review(approved=False, feedback="no")


def test_success_run_emits_attempt_result_final() -> None:
    events: list[AgentEvent] = []
    agent = AutonomousAgent(
        _OkWorker(),
        on_event=events.append,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    agent.run("do it")
    kinds = [e.kind for e in events]
    assert kinds == ["status", "attempt", "result", "final"]
    assert events[2].data["success"] is True
    assert events[3].data == {"success": True, "answer": "done"}


def test_failed_run_emits_final_failure() -> None:
    events: list[AgentEvent] = []
    agent = AutonomousAgent(
        _FailWorker(),
        manager=_RejectManager(),
        on_event=events.append,
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=True),
    )
    agent.run("do it")
    assert [e.kind for e in events].count("attempt") == 2  # both attempts announced
    assert events[-1].kind == "final" and events[-1].data["success"] is False


def test_broken_sink_never_breaks_the_run() -> None:
    def _boom(event: AgentEvent) -> None:
        raise RuntimeError("sink is broken")

    agent = AutonomousAgent(
        _OkWorker(),
        on_event=_boom,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    assert agent.run("do it").success is True  # sink errors are swallowed


def test_no_sink_is_a_noop() -> None:
    agent = AutonomousAgent(
        _OkWorker(), config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False)
    )
    assert agent.run("do it").success is True  # on_event=None path
