"""Tests for durable execution (M13 C3) — RunCheckpointer + solve-loop resume."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.planner import Planner
from chimera.core.runstate import RunCheckpointer
from chimera.evolution.stagnation import StagnationDetector
from chimera.orchestration.budget import SpendBudget
from chimera.orchestration.metering import MeteredBackend
from chimera.providers.gateway import CompletionResult, MessageLike

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


class _BudgetedWorker:
    def __init__(self, cap: float) -> None:
        self.config = SimpleNamespace(max_usd=cap)
        self.seen_spend: list[float] = []

    def run(self, task: str, *, spend: SpendBudget) -> AgentResult:
        self.seen_spend.append(spend.spent)
        if reason := spend.blocked():
            return AgentResult(answer=reason, steps=0, stopped_reason="spend", usd=0.0)
        spend.charge(0.03)
        return AgentResult(answer="attempt", steps=1, stopped_reason="final", usd=0.03)


class _PaidPlannerBackend:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(
            content=f"1. plan {self.calls}",
            model="openrouter/deepseek/deepseek-chat-v3.1",
            prompt_tokens=120,
            completion_tokens=15,
            usd=0.02,
        )


@pytest.mark.parametrize(
    ("next_index", "cap", "cancel_on_resume"),
    [(1, 0.02, False), (3, 0.10, False), (3, 0.10, True)],
)
def test_resume_keeps_the_budget_and_usage_of_the_saved_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, next_index: int, cap: float, cancel_on_resume: bool,
) -> None:
    monkeypatch.setenv("CHIMERA_STRICT_SPEND_CAP", "false")

    class _CrashAtSavedPlan(RunCheckpointer):
        def save(self, thread_id: str, state: dict[str, Any]) -> None:
            super().save(thread_id, state)
            if state["next_index"] == next_index:
                raise RuntimeError("stopped after the plan reached disk")

    db = tmp_path / "runs.db"
    config = AutonomousConfig(max_attempts=3, use_planner=True, use_manager=True)
    backend = _PaidPlannerBackend()
    meter = MeteredBackend(backend)
    first_worker = _BudgetedWorker(cap)
    first = AutonomousAgent(
        first_worker,
        planner=Planner(meter),
        manager=_RejectOnce(approve_from=99),  # type: ignore[arg-type]  # Offline reviewer.
        meter=meter,
        checkpointer=_CrashAtSavedPlan(db),
        stagnation=StagnationDetector(window=2),
        replan_on_stall=True,
        config=config,
    )
    with pytest.raises(RuntimeError, match="stopped after the plan reached disk"):
        first.run("task", thread_id="job")
    assert len(first_worker.seen_spend) == next_index - 1
    expected_plans = 1 if next_index == 1 else 2
    assert backend.calls == expected_plans

    store = RunCheckpointer(db)
    fresh_backend = _PaidPlannerBackend()
    fresh_meter = MeteredBackend(fresh_backend)
    resumed_worker = _BudgetedWorker(cap)
    resumed = AutonomousAgent(
        resumed_worker, planner=Planner(fresh_meter), meter=fresh_meter,
        checkpointer=store, config=config, should_stop=lambda: cancel_on_resume,
    ).run("task", thread_id="job")

    assert fresh_backend.calls == 0
    assert resumed.plan is not None and resumed.plan.steps == [f"plan {expected_plans}"]
    if cancel_on_resume:
        assert resumed_worker.seen_spend == []
        assert resumed.stopped_reason == "cancelled"
        assert resumed.answer == "attempt"
    else:
        assert resumed_worker.seen_spend == pytest.approx([cap])
        assert resumed.stopped_reason == "spend"
    assert sum(a.usd or 0.0 for a in resumed.attempts) == pytest.approx(cap)
    assert [a.overhead_usd for a in resumed.attempts if a.overhead_usd] == [0.02] * expected_plans
    assert sum(a.prompt_tokens for a in resumed.attempts) == 120 * expected_plans
    assert sum(a.completion_tokens for a in resumed.attempts) == 15 * expected_plans
    assert store.load("job") is None


def test_legacy_checkpoint_charges_restored_attempt_including_its_overhead_once(tmp_path: Path) -> None:
    store = RunCheckpointer(tmp_path / "runs.db")
    store.save("job", {
        "next_index": 2,
        "attempts": [{
            "index": 1, "answer": "attempt", "approved": False, "verified": False,
            "reverted": False, "usd": 0.09, "overhead_usd": 0.04,
        }],
    })
    worker = _BudgetedWorker(cap=0.10)
    resumed = AutonomousAgent(
        worker, checkpointer=store, config=AutonomousConfig(max_attempts=2, use_planner=False),
    ).run("task", thread_id="job")

    assert resumed.success
    assert worker.seen_spend == [0.09]
    assert [a.usd for a in resumed.attempts] == [0.09, 0.03]


@pytest.mark.parametrize("stop_check", [1, 2])
def test_cancel_after_planning_writes_the_planner_usage_to_the_receipt(
    tmp_path: Path, stop_check: int,
) -> None:
    backend = _PaidPlannerBackend()
    meter = MeteredBackend(backend)
    worker = _CountingWorker(0)
    run_log = tmp_path / "runs.jsonl"
    store = RunCheckpointer(tmp_path / "runs.db")
    checks = 0

    def should_stop() -> bool:
        nonlocal checks
        checks += 1
        return backend.calls > 0 and checks >= stop_check

    result = AutonomousAgent(
        worker, planner=Planner(meter), meter=meter, should_stop=should_stop,
        run_log=run_log, checkpointer=store,
        config=AutonomousConfig(max_attempts=1, use_planner=True),
    ).run("task", thread_id="job")

    assert result.stopped_reason == "cancelled"
    assert backend.calls == 1 and worker.runs == 0
    assert store.load("job") is None
    row = json.loads(run_log.read_text(encoding="utf-8"))
    assert row["ending"] == "cancelled"
    assert row["usd"] == 0.02
    [attempt] = row["attempts"]
    assert attempt["overhead_usd"] == 0.02
    assert attempt["prompt_tokens"] == 120
    assert attempt["completion_tokens"] == 15
    assert not attempt["requested_model"]


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
