"""Tests for the live PROBE wiring (M18-5): ProbeLog persistence + AutonomousAgent recording."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import AutonomousAgent, AutonomousConfig
from chimera.core.agent import AgentResult
from chimera.core.supervisor import Review
from chimera.core.verify import VerificationResult
from chimera.fusion.probe_log import ProbeLog

# --- ProbeLog persistence ---------------------------------------------------------------


def test_record_and_read_back(tmp_path: Path) -> None:
    log = ProbeLog(tmp_path / "p.jsonl")
    log.record(arm="worker", proxy=1.0, reward=1.0)
    log.record(arm="worker", proxy=0.0, reward=0.0)
    log.record(arm="escalate", proxy=1.0, reward=None)  # cheap-only draw
    obs = log.observations()
    assert obs == {"worker": [(1.0, 1.0), (0.0, 0.0)], "escalate": [(1.0, None)]}


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert ProbeLog(tmp_path / "nope.jsonl").observations() == {}


def test_corrupt_line_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    path.write_text('{"arm": "a", "proxy": 0.5, "reward": 1.0}\nnot json\n{"arm": "a", "proxy": 0.2, "reward": 0.0}\n', encoding="utf-8")
    assert ProbeLog(path).observations() == {"a": [(0.5, 1.0), (0.2, 0.0)]}


# --- AutonomousAgent records (arm, proxy=manager, reward=verified) -----------------------


class _Worker:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final")


class _ApproveManager:
    def review(self, task: str, answer: str, context: str) -> Any:
        return Review(approved=True, feedback="")


class _FlakyVerifier:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def verify(self) -> VerificationResult:
        self.calls += 1
        passed = self.calls > self.fail_times
        return VerificationResult(passed=passed, output="" if passed else "failed")


def test_agent_records_paired_observation(tmp_path: Path) -> None:
    log = ProbeLog(tmp_path / "probe.jsonl")
    auto = AutonomousAgent(
        _Worker("done"),
        verifier=_FlakyVerifier(fail_times=0),  # passes on attempt 1
        manager=_ApproveManager(),  # type: ignore[arg-type]
        probe_log=log,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=True),
    )
    auto.run("t")
    # manager approved (proxy 1.0) + verified (reward 1.0) for the base worker arm.
    assert log.observations() == {"worker": [(1.0, 1.0)]}


def test_agent_labels_worker_vs_escalate_arms(tmp_path: Path) -> None:
    log = ProbeLog(tmp_path / "probe.jsonl")
    auto = AutonomousAgent(
        _Worker("cheap"),
        escalate_worker=_Worker("fused"),
        verifier=_FlakyVerifier(fail_times=1),  # attempt 1 (worker) fails, attempt 2 (escalate) passes
        manager=_ApproveManager(),  # type: ignore[arg-type]
        probe_log=log,
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=True),
    )
    auto.run("t")
    obs = log.observations()
    assert obs["worker"] == [(1.0, 0.0)]   # failed verify on the base worker
    assert obs["escalate"] == [(1.0, 1.0)]  # the escalated retry passed


def test_no_probe_log_records_nothing(tmp_path: Path) -> None:
    log = ProbeLog(tmp_path / "probe.jsonl")
    auto = AutonomousAgent(
        _Worker("done"),
        verifier=_FlakyVerifier(fail_times=0),
        manager=_ApproveManager(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=True),
    )  # no probe_log
    auto.run("t")
    assert log.observations() == {}
