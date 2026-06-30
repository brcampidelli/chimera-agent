"""Tests for the Tier-2 AutonomousAgent (plan / execute / supervise / verify-revert)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core import (
    AutonomousAgent,
    AutonomousConfig,
    Manager,
    Planner,
    WorkspaceGuard,
)
from chimera.core.agent import AgentResult
from chimera.core.verify import VerificationResult
from chimera.evolution import ExperienceBuffer
from chimera.providers import CompletionResult


class FakeWorker:
    """A worker that optionally writes a file each run and returns a fixed answer."""

    def __init__(self, answer: str = "done", *, workspace: Path | None = None, filename: str | None = None) -> None:
        self.answer = answer
        self.workspace = workspace
        self.filename = filename
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        if self.workspace and self.filename:
            (self.workspace / self.filename).write_text("content", encoding="utf-8")
        return AgentResult(answer=self.answer, steps=1, stopped_reason="final")


class FlakyVerifier:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def verify(self) -> VerificationResult:
        self.calls += 1
        passed = self.calls > self.fail_times
        return VerificationResult(passed=passed, output="" if passed else "tests failed")


class FailVerifier:
    def verify(self) -> VerificationResult:
        return VerificationResult(False, "always fails")


class ScriptedBackend:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        content = self._contents.pop(0) if self._contents else "APPROVED"
        return CompletionResult(content=content, model="fake")


class FixedBackend:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content=self.content, model="fake")


def test_success_on_first_attempt() -> None:
    auto = AutonomousAgent(FakeWorker("answer"), config=AutonomousConfig(use_planner=False))
    result = auto.run("do something")
    assert result.success is True
    assert len(result.attempts) == 1
    assert result.answer == "answer"


def test_verify_revert_then_success(tmp_path: Path) -> None:
    worker = FakeWorker(workspace=tmp_path, filename="out.txt")
    auto = AutonomousAgent(
        worker,
        verifier=FlakyVerifier(fail_times=1),
        guard=WorkspaceGuard(tmp_path),
        config=AutonomousConfig(use_planner=False),
    )
    result = auto.run("create out.txt")

    assert result.success is True
    assert len(result.attempts) == 2
    assert result.attempts[0].verified is False and result.attempts[0].reverted is True
    assert result.attempts[1].success is True
    assert (tmp_path / "out.txt").exists()  # kept from the successful attempt


def test_always_fail_reverts_everything(tmp_path: Path) -> None:
    worker = FakeWorker(workspace=tmp_path, filename="out.txt")
    auto = AutonomousAgent(
        worker,
        verifier=FailVerifier(),
        guard=WorkspaceGuard(tmp_path),
        config=AutonomousConfig(max_attempts=2, use_planner=False),
    )
    result = auto.run("create out.txt")

    assert result.success is False
    assert len(result.attempts) == 2
    assert all(a.reverted for a in result.attempts)
    assert not (tmp_path / "out.txt").exists()  # last attempt was reverted


def test_manager_revision_then_approval() -> None:
    manager = Manager(ScriptedBackend(["REVISE: handle empty input", "APPROVED"]))
    auto = AutonomousAgent(
        FakeWorker("ans"),
        manager=manager,
        config=AutonomousConfig(use_planner=False),
    )
    result = auto.run("task")

    assert result.success is True
    assert len(result.attempts) == 2
    assert result.attempts[0].approved is False
    assert result.attempts[0].feedback == "handle empty input"
    assert result.attempts[1].approved is True


def test_passing_verifier_overrides_manager_rejection(tmp_path: Path) -> None:
    # Executable evidence is ground truth: a strict Manager that rejects (e.g. it
    # judged the narration, not the artifact) must NOT revert verified-correct work.
    worker = FakeWorker(workspace=tmp_path, filename="out.txt")
    auto = AutonomousAgent(
        worker,
        manager=Manager(FixedBackend("REVISE: I cannot confirm the file exists")),
        verifier=FlakyVerifier(fail_times=0),  # always passes
        guard=WorkspaceGuard(tmp_path),
        config=AutonomousConfig(use_planner=False),
    )
    result = auto.run("create out.txt")

    assert result.success is True
    assert len(result.attempts) == 1
    assert result.attempts[0].reverted is False
    assert (tmp_path / "out.txt").exists()


def test_trajectories_recorded(tmp_path: Path) -> None:
    from chimera.ecosystem import TrajectoryCollector

    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    auto = AutonomousAgent(
        FakeWorker("ans"), trajectories=collector, config=AutonomousConfig(use_planner=False)
    )
    auto.run("do X")
    items = collector.all()
    assert len(items) == 1
    assert items[0].prompt == "do X"
    assert items[0].outcome == "success"
    assert items[0].reward == 1.0


def test_experience_recorded(tmp_path: Path) -> None:
    buf = ExperienceBuffer(tmp_path / "exp.json")
    auto = AutonomousAgent(FakeWorker(), experience=buf, config=AutonomousConfig(use_planner=False))
    auto.run("task")
    assert len(buf.successes()) == 1


def test_plan_is_attached() -> None:
    auto = AutonomousAgent(
        FakeWorker(),
        planner=Planner(FixedBackend("1. do it\n2. verify")),
        config=AutonomousConfig(use_planner=True),
    )
    result = auto.run("task")
    assert result.plan is not None
    assert result.plan.steps == ["do it", "verify"]
