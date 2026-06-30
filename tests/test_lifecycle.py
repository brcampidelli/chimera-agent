"""Tests for the SDLC lifecycle crew (plan -> build -> test -> review)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.agent import AgentResult
from chimera.core.checkpoint import WorkspaceGuard
from chimera.core.verify import VerificationResult
from chimera.orchestration import LifecycleCrew
from chimera.providers import CompletionResult


class FakeWorker:
    def __init__(self, *, workspace: Path, filename: str) -> None:
        self.workspace = workspace
        self.filename = filename

    def run(self, task: str) -> AgentResult:
        (self.workspace / self.filename).write_text("content", encoding="utf-8")
        return AgentResult(answer="built it", steps=1, stopped_reason="final")


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


class FixedBackend:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content=self.content, model="fake")


def test_lifecycle_runs_all_stages_and_passes(tmp_path: Path) -> None:
    worker = FakeWorker(workspace=tmp_path, filename="out.txt")
    crew = LifecycleCrew(
        worker,
        FixedBackend("1. build it\n2. verify it"),
        verifier=FlakyVerifier(fail_times=1),  # fails once, then passes (verify-or-revert)
        guard=WorkspaceGuard(tmp_path),
        max_build_attempts=3,
    )
    result = crew.run("create out.txt")

    assert [s.name for s in result.stages] == ["plan", "build", "test", "review"]
    assert result.success is True
    assert next(s for s in result.stages if s.name == "test").passed is True
    assert (tmp_path / "out.txt").exists()  # kept from the verified build


def test_lifecycle_test_stage_fails_when_verification_fails(tmp_path: Path) -> None:
    worker = FakeWorker(workspace=tmp_path, filename="out.txt")
    crew = LifecycleCrew(
        worker,
        FixedBackend("1. attempt it"),
        verifier=FailVerifier(),
        guard=WorkspaceGuard(tmp_path),
        max_build_attempts=2,
    )
    result = crew.run("do it")

    assert result.success is False
    assert next(s for s in result.stages if s.name == "test").passed is False
    assert any(s.name == "review" for s in result.stages)  # review still runs (advisory)
    assert not (tmp_path / "out.txt").exists()  # reverted — nothing kept
