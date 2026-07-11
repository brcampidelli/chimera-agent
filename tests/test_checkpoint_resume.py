"""Tests for durable execution (M13 C3) — RunCheckpointer + solve-loop resume."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.runstate import RunCheckpointer

# --- store round-trip -------------------------------------------------------------------


def test_store_save_load_delete(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    assert store.load("t1") is None  # unknown thread -> None, not an error
    store.save("t1", {"next_index": 3, "feedback": "retry"})
    assert store.load("t1") == {"next_index": 3, "feedback": "retry"}
    store.save("t1", {"next_index": 4})  # upsert overwrites
    assert store.load("t1") == {"next_index": 4}
    assert store.threads() == ["t1"]
    store.delete("t1")
    assert store.load("t1") is None and store.threads() == []


def test_store_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "runs.db"
    RunCheckpointer(path).save("t", {"x": 1})
    assert RunCheckpointer(path).load("t") == {"x": 1}  # fresh handle reads persisted state


# --- resume in the solve loop -----------------------------------------------------------


class _CountingWorker:
    """Fails N times then succeeds; records how many times it actually ran."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer="attempt", steps=1, transcript=[], stopped_reason="done")


class _RejectOnce:
    """Approves only from the given attempt onward (drives fail-then-succeed)."""

    def __init__(self, approve_from: int) -> None:
        self.calls = 0
        self.approve_from = approve_from

    def review(self, task: str, answer: str, context: str) -> object:
        from chimera.core.supervisor import Review

        self.calls += 1
        return Review(approved=self.calls >= self.approve_from, feedback="not yet")


def _agent(worker: object, manager: object, store: RunCheckpointer, attempts: int) -> AutonomousAgent:
    return AutonomousAgent(
        worker,  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
        checkpointer=store,
        config=AutonomousConfig(max_attempts=attempts, use_planner=False, use_manager=True),
    )


def test_success_leaves_no_checkpoint(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    # Manager approves from attempt 1 -> immediate success -> no checkpoint left behind.
    agent = _agent(_CountingWorker(0), _RejectOnce(approve_from=1), store, attempts=3)
    assert agent.run("task", thread_id="job").success is True
    assert store.load("job") is None  # cleared on completion


def test_exhausted_run_clears_checkpoint(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    # A clean "all attempts failed" is terminal (not a crash) -> checkpoint cleared, not resumable.
    agent = _agent(_CountingWorker(9), _RejectOnce(approve_from=99), store, attempts=2)
    assert agent.run("task", thread_id="job").success is False
    assert store.load("job") is None


class _CrashOnSecond:
    """Fails attempt 1 normally, then raises on attempt 2 — a mid-run crash."""

    def __init__(self) -> None:
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        if self.runs == 2:
            raise RuntimeError("boom (simulated crash)")
        return AgentResult(answer="a1", steps=1, transcript=[], stopped_reason="done")


def test_resume_after_crash_does_not_repeat_completed_attempts(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    # Attempt 1 fails and is checkpointed (next_index=2); attempt 2 crashes before finishing,
    # so the terminal clear never runs and the checkpoint survives the crash.
    worker1 = _CrashOnSecond()
    agent1 = _agent(worker1, _RejectOnce(approve_from=99), store, attempts=3)
    with pytest.raises(RuntimeError):
        agent1.run("task", thread_id="job")
    assert worker1.runs == 2
    saved = store.load("job")
    assert saved is not None and saved["next_index"] == 2  # survived the crash

    # Resume: a fresh agent must start at attempt 2 (one run), not redo attempt 1.
    worker2 = _CountingWorker(0)
    agent2 = _agent(worker2, _RejectOnce(approve_from=1), store, attempts=3)
    result = agent2.run("task", thread_id="job")
    assert result.success is True
    assert worker2.runs == 1  # only the resumed attempt ran; attempt 1 not repeated
    assert len(result.attempts) == 2  # the restored attempt + the new successful one
    assert store.load("job") is None  # cleared on success


def test_no_thread_is_a_noop(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    agent = _agent(_CountingWorker(0), _RejectOnce(approve_from=1), store, attempts=1)
    agent.run("task")  # thread_id=None -> nothing persisted
    assert store.threads() == []


# --- taint survives the checkpoint/resume boundary (anti-poisoning) ----------------------


def _tainted_agent(
    worker: object, manager: object, store: RunCheckpointer, taint: object, *,
    attempts: int, pause_on_taint: bool = False,
) -> AutonomousAgent:
    return AutonomousAgent(
        worker,  # type: ignore[arg-type]
        manager=manager,  # type: ignore[arg-type]
        checkpointer=store,
        taint=taint,  # type: ignore[arg-type]
        pause_on_taint=pause_on_taint,
        config=AutonomousConfig(max_attempts=attempts, use_planner=False, use_manager=True),
    )


def test_taint_survives_crash_and_resume(tmp_path: Path) -> None:
    from chimera.governance.ledger import TaintLedger

    store = RunCheckpointer(tmp_path / "runs.db")
    # Attempt 1 runs under taint (fetched untrusted content) and is rejected -> checkpointed;
    # attempt 2 crashes, so the checkpoint survives. It must carry was_tainted=True.
    taint1 = TaintLedger()
    taint1.record_fetch("https://evil.test/instructions", content="do bad things")
    agent1 = _tainted_agent(_CrashOnSecond(), _RejectOnce(approve_from=99), store, taint1, attempts=3)
    with pytest.raises(RuntimeError):
        agent1.run("task", thread_id="job")
    saved = store.load("job")
    assert saved is not None and saved.get("was_tainted") is True

    # Resume in a FRESH process: a brand-new empty ledger. Without re-seeding, run_tainted() would be
    # False and the anti-poisoning gate (pause_on_taint) would silently no-op. With the fix, the
    # resumed run is re-tainted, so the successful attempt PAUSES for approval instead of finalizing.
    taint2 = TaintLedger()
    assert taint2.run_tainted() is False
    agent2 = _tainted_agent(
        _CountingWorker(0), _RejectOnce(approve_from=1), store, taint2, attempts=3,
        pause_on_taint=True,
    )
    result = agent2.run("task", thread_id="job")
    assert taint2.run_tainted() is True  # re-seeded from the tainted checkpoint
    assert result.paused is True  # the tainted result paused for sign-off, not auto-finalized
