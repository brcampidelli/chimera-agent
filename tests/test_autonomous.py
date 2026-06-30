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


def _tool_error_transcript(tool: str, error: str) -> list[Any]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool}}]},
        {"role": "tool", "content": error},
    ]


def test_fault_hint_localizes_failed_tool_step() -> None:
    result = AgentResult(
        answer="x",
        steps=2,
        stopped_reason="final",
        transcript=_tool_error_transcript("run_shell", "error: command exited 1"),
    )
    hint = AutonomousAgent._fault_hint(result)
    assert "run_shell" in hint and "exited 1" in hint


def test_fault_hint_empty_when_no_tool_error() -> None:
    result = AgentResult(answer="x", steps=1, stopped_reason="final", transcript=[])
    assert AutonomousAgent._fault_hint(result) == ""


def test_retry_feedback_includes_step_level_diagnosis() -> None:
    class FaultingWorker:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run(self, task: str) -> AgentResult:
            self.prompts.append(task)
            return AgentResult(
                answer="ans",
                steps=1,
                stopped_reason="final",
                transcript=_tool_error_transcript("write_file", "error: permission denied"),
            )

    worker = FaultingWorker()
    auto = AutonomousAgent(
        worker,
        verifier=FlakyVerifier(fail_times=1),  # attempt 1 fails, attempt 2 passes
        config=AutonomousConfig(use_planner=False, use_manager=False, max_attempts=2),
    )
    result = auto.run("do X")
    assert result.success is True
    # the retry prompt must carry the localized first-fault from attempt 1
    assert "write_file" in worker.prompts[1]
    assert "permission denied" in worker.prompts[1]


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


def test_trajectory_records_steps(tmp_path: Path) -> None:
    from chimera.ecosystem import TrajectoryCollector

    collector = TrajectoryCollector(tmp_path / "traj.jsonl")
    auto = AutonomousAgent(
        FakeWorker("ans"), trajectories=collector, config=AutonomousConfig(use_planner=False)
    )
    auto.run("do X")
    assert collector.all()[0].steps == 1  # FakeWorker reports steps=1 (long-horizon signal)


def test_experience_recorded(tmp_path: Path) -> None:
    buf = ExperienceBuffer(tmp_path / "exp.json")
    auto = AutonomousAgent(FakeWorker(), experience=buf, config=AutonomousConfig(use_planner=False))
    auto.run("task")
    assert len(buf.successes()) == 1


def test_experience_is_recalled_into_planner_and_worker(tmp_path: Path) -> None:
    # Seed a relevant prior failure (+ an unrelated success that must NOT be pulled in).
    buf = ExperienceBuffer(tmp_path / "exp.json")
    buf.record("create config file with retries", "failure", detail="forgot the retry flag")
    buf.record("paint the garden fence", "success", detail="looks great")

    class RecordingWorker:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def run(self, task: str) -> AgentResult:
            self.prompts.append(task)
            return AgentResult(answer="ok", steps=1, stopped_reason="final")

    class RecordingBackend:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            self.seen.append(messages[-1].content)  # the user message
            return CompletionResult(content="1. do it", model="fake")

    worker = RecordingWorker()
    backend = RecordingBackend()
    auto = AutonomousAgent(
        worker,
        planner=Planner(backend),
        experience=buf,
        config=AutonomousConfig(use_planner=True),
    )
    auto.run("create config file with retries")

    assert any("forgot the retry flag" in s for s in backend.seen)  # planner saw the lesson
    assert any("forgot the retry flag" in p for p in worker.prompts)  # worker saw it too
    assert all("paint the garden fence" not in p for p in worker.prompts)  # unrelated excluded


class RecordingMemory:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str | None]] = []

    def remember(self, content: str, *, key: str | None = None) -> object:
        self.saved.append((content, key))
        return ("ADD", None)


def test_remembers_on_verified_success() -> None:
    mem = RecordingMemory()
    auto = AutonomousAgent(
        FakeWorker("the answer"), memory=mem, config=AutonomousConfig(use_planner=False)
    )
    auto.run("ship the feature")
    assert len(mem.saved) == 1
    content, key = mem.saved[0]
    assert "ship the feature" in content
    assert key == "solve:ship-the-feature"  # deduped by a stable per-task key


def test_does_not_remember_on_failure(tmp_path: Path) -> None:
    mem = RecordingMemory()
    auto = AutonomousAgent(
        FakeWorker(workspace=tmp_path, filename="x.txt"),
        verifier=FailVerifier(),
        guard=WorkspaceGuard(tmp_path),
        memory=mem,
        config=AutonomousConfig(max_attempts=2, use_planner=False),
    )
    result = auto.run("do the thing")
    assert result.success is False
    assert mem.saved == []  # verify-or-revert gate: unverified work is never memorised


class RecordingEvolver:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def maybe_evolve(self, task: str, solution: str, prior_successes: int) -> object:
        self.calls.append((task, solution, prior_successes))
        return None


def test_auto_evolver_fired_on_success_with_recurrence_count(tmp_path: Path) -> None:
    buf = ExperienceBuffer(tmp_path / "exp.json")
    buf.record("build the weekly report", "success", "")
    buf.record("build the weekly report", "success", "")  # 2 prior successes
    evolver = RecordingEvolver()
    auto = AutonomousAgent(
        FakeWorker("ok"),
        experience=buf,
        auto_evolver=evolver,
        config=AutonomousConfig(use_planner=False),
    )
    auto.run("build the weekly report")
    assert len(evolver.calls) == 1
    assert evolver.calls[0][2] == 2  # prior successes counted before this run recorded its own


def test_auto_evolver_not_fired_on_failure(tmp_path: Path) -> None:
    evolver = RecordingEvolver()
    auto = AutonomousAgent(
        FakeWorker(workspace=tmp_path, filename="x.txt"),
        verifier=FailVerifier(),
        guard=WorkspaceGuard(tmp_path),
        auto_evolver=evolver,
        config=AutonomousConfig(max_attempts=1, use_planner=False),
    )
    assert auto.run("do X").success is False
    assert evolver.calls == []  # no skill evolution from unverified work


def test_plan_is_attached() -> None:
    auto = AutonomousAgent(
        FakeWorker(),
        planner=Planner(FixedBackend("1. do it\n2. verify")),
        config=AutonomousConfig(use_planner=True),
    )
    result = auto.run("task")
    assert result.plan is not None
    assert result.plan.steps == ["do it", "verify"]
