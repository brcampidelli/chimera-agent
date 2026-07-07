"""Tests for the HITL {accept, edit, respond, ignore} envelope over the taint-pause (M15-B2)."""

from __future__ import annotations

from pathlib import Path

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.runstate import RunCheckpointer


def _paused(cp: RunCheckpointer, thread: str, answer: str = "draft answer") -> None:
    """Seed a checkpoint in the awaiting-approval state (as pause_on_taint would leave it)."""
    cp.save(thread, {
        "task": "do the thing", "next_index": 2, "feedback": "", "attempts": [],
        "awaiting_approval": True, "paused_answer": answer, "was_tainted": True,
    })


# --- the checkpointer envelope -----------------------------------------------------------


def test_accept_marks_approved(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t")
    assert cp.respond("t", "accept") is True
    state = cp.load("t")
    assert state is not None and state["approved"] is True and state["awaiting_approval"] is False


def test_edit_finalizes_the_corrected_answer(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t", answer="wrong")
    assert cp.respond("t", "edit", answer="the corrected answer") is True
    state = cp.load("t")
    assert state is not None and state["approved"] is True
    assert state["paused_answer"] == "the corrected answer"  # human's edit, not the model's


def test_respond_injects_feedback_and_does_not_finalize(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t")
    assert cp.respond("t", "respond", feedback="use the retry flag") is True
    state = cp.load("t")
    assert state is not None
    assert state["approved"] is False and state["paused_answer"] is None  # resume, don't finalize
    assert "use the retry flag" in state["feedback"]


def test_ignore_denies(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t")
    assert cp.respond("t", "ignore") is True
    state = cp.load("t")
    assert state is not None and state["denied"] is True and state["approved"] is False


def test_unknown_action_and_not_awaiting_are_rejected(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t")
    assert cp.respond("t", "bogus") is False
    cp.save("clean", {"task": "x"})  # not awaiting approval
    assert cp.respond("clean", "accept") is False
    assert cp.respond("ghost", "accept") is False  # no such thread


def test_approve_is_accept_shim(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t")
    assert cp.approve("t") is True
    assert cp.load("t")["approved"] is True  # type: ignore[index]


# --- resume behavior in the autonomous loop ----------------------------------------------


class _Worker:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, task: str) -> AgentResult:
        self.calls += 1
        return AgentResult(answer=f"solved:{task}", steps=1, stopped_reason="final")


def test_accept_finalizes_without_rerunning(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t", answer="the reviewed answer")
    cp.respond("t", "accept")
    worker = _Worker()
    auto = AutonomousAgent(worker, checkpointer=cp, config=AutonomousConfig(use_planner=False))
    result = auto.run("do the thing", thread_id="t")
    assert result.success is True
    assert result.answer == "the reviewed answer"  # the exact reviewed output, as-is
    assert worker.calls == 0  # accept never re-runs the model


def test_edit_finalizes_the_human_answer(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t", answer="model draft")
    cp.respond("t", "edit", answer="human fixed it")
    auto = AutonomousAgent(_Worker(), checkpointer=cp, config=AutonomousConfig(use_planner=False))
    result = auto.run("do the thing", thread_id="t")
    assert result.success is True and result.answer == "human fixed it"


def test_ignore_ends_the_run_denied(tmp_path: Path) -> None:
    cp = RunCheckpointer(tmp_path / "runs.db")
    _paused(cp, "t", answer="tainted output")
    cp.respond("t", "ignore")
    worker = _Worker()
    auto = AutonomousAgent(worker, checkpointer=cp, config=AutonomousConfig(use_planner=False))
    result = auto.run("do the thing", thread_id="t")
    assert result.success is False
    assert result.answer == ""  # the flagged output is never finalized
    assert worker.calls == 0
    assert cp.load("t") is None  # thread cleared
