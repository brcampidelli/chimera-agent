"""Tests for the dual-ledger re-plan (M13 B3) — TaskLedger + stall-triggered re-planning."""

from __future__ import annotations

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.ledger import TaskLedger
from chimera.core.planner import Plan
from chimera.evolution.stagnation import StagnationDetector

# --- TaskLedger unit --------------------------------------------------------------------


def test_task_ledger_dedupes_and_renders() -> None:
    ledger = TaskLedger(task="t")
    ledger.add_guess("timeout on the API")
    ledger.add_guess("timeout on the API")  # duplicate ignored
    ledger.add_guess("  ")  # blank ignored
    ledger.add_guess("wrong endpoint")
    assert ledger.guesses == ["timeout on the API", "wrong endpoint"]
    ctx = ledger.context()
    assert "do NOT repeat" in ctx and "timeout on the API" in ctx and "wrong endpoint" in ctx


def test_task_ledger_empty_context() -> None:
    assert TaskLedger(task="t").context() == ""


def test_task_ledger_summary_counts_replans() -> None:
    ledger = TaskLedger(task="t")
    ledger.note_replan()
    ledger.add_guess("g")
    assert "re-plan #1" in ledger.summary() and "1 failure cause" in ledger.summary()


# --- integration: stall triggers a re-plan with accumulated cause -----------------------


class _FailWorker:
    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="nope", steps=1, transcript=[], stopped_reason="done")


class _RejectManager:
    def review(self, task: str, answer: str, context: str) -> object:
        from chimera.core.supervisor import Review

        return Review(approved=False, feedback="same failure every time")


class _RecordingPlanner:
    """A planner that records the context of every plan() call."""

    def __init__(self) -> None:
        self.contexts: list[str] = []

    def plan(self, task: str, *, context: str = "") -> Plan:
        self.contexts.append(context)
        return Plan(steps=["step one"])


def test_stall_triggers_replan_with_accumulated_cause() -> None:
    planner = _RecordingPlanner()
    agent = AutonomousAgent(
        _FailWorker(),
        planner=planner,
        manager=_RejectManager(),
        stagnation=StagnationDetector(window=2),  # 2 identical failures = stall
        replan_on_stall=True,
        config=AutonomousConfig(max_attempts=3, use_planner=True, use_manager=True),
    )
    agent.run("do the thing")
    # First plan() is the initial plan; a re-plan fires once the window-2 stall is detected.
    assert len(planner.contexts) >= 2
    # The re-plan context carries the accumulated failure cause the first plan never had.
    assert any("do NOT repeat" in ctx for ctx in planner.contexts[1:])


def test_no_replan_without_flag_falls_back_to_advice() -> None:
    planner = _RecordingPlanner()
    agent = AutonomousAgent(
        _FailWorker(),
        planner=planner,
        manager=_RejectManager(),
        stagnation=StagnationDetector(window=2),
        replan_on_stall=False,  # advisory pivot only
        config=AutonomousConfig(max_attempts=3, use_planner=True, use_manager=True),
    )
    agent.run("do the thing")
    assert len(planner.contexts) == 1  # only the initial plan; no re-plan
