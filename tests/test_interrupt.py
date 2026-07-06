"""Tests for human-in-the-loop interrupt/resume (M13 C4) — pause on tainted, approve/deny."""

from __future__ import annotations

from pathlib import Path

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.runstate import RunCheckpointer


class _OkWorker:
    def __init__(self) -> None:
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer="the result", steps=1, transcript=[], stopped_reason="done")


class _Taint:
    """A taint ledger stub: reports whether the run consumed untrusted content."""

    def __init__(self, tainted: bool) -> None:
        self._tainted = tainted

    def run_tainted(self) -> bool:
        return self._tainted


def _agent(worker: object, store: RunCheckpointer, *, taint: _Taint, pause: bool) -> AutonomousAgent:
    return AutonomousAgent(
        worker,  # type: ignore[arg-type]
        taint=taint,  # type: ignore[arg-type]
        checkpointer=store,
        pause_on_taint=pause,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )


def test_tainted_run_pauses_for_approval(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    result = _agent(_OkWorker(), store, taint=_Taint(True), pause=True).run("t", thread_id="job")
    assert result.paused is True and result.success is False
    saved = store.load("job")
    assert saved is not None
    assert saved["awaiting_approval"] is True
    assert saved["paused_answer"] == "the result" and saved["was_tainted"] is True


def test_untainted_run_does_not_pause(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    result = _agent(_OkWorker(), store, taint=_Taint(False), pause=True).run("t", thread_id="job")
    assert result.success is True and result.paused is False
    assert store.load("job") is None  # completed normally, no checkpoint


def test_pause_off_finalizes_even_when_tainted(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    result = _agent(_OkWorker(), store, taint=_Taint(True), pause=False).run("t", thread_id="job")
    assert result.success is True and result.paused is False


def test_approve_finalizes_without_rerunning(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    worker = _OkWorker()
    # First run pauses.
    _agent(worker, store, taint=_Taint(True), pause=True).run("t", thread_id="job")
    assert worker.runs == 1

    # Human approves, then a resume finalizes the reviewed answer as-is (worker not re-run).
    assert store.approve("job") is True
    resumed = _agent(worker, store, taint=_Taint(True), pause=True).run("t", thread_id="job")
    assert resumed.success is True and resumed.paused is False
    assert resumed.answer == "the result"
    assert worker.runs == 1  # NOT re-executed — approval is of the exact output
    assert store.load("job") is None  # cleared after finalizing


def test_deny_is_just_dropping_the_checkpoint(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    _agent(_OkWorker(), store, taint=_Taint(True), pause=True).run("t", thread_id="job")
    assert store.load("job") is not None
    store.delete("job")  # what `solve --deny` does
    assert store.load("job") is None


def test_approve_requires_awaiting_state(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    assert store.approve("nope") is False  # no checkpoint
    store.save("plain", {"next_index": 2})  # a non-paused checkpoint
    assert store.approve("plain") is False  # not awaiting approval
