"""Tests for gated independent strong verification (M14 B4)."""

from __future__ import annotations

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.strong_verify import StrongVerifier, _parse_grade
from chimera.providers.gateway import CompletionResult


class _Grader:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.content, model="strong")


# --- StrongVerifier ---------------------------------------------------------------------


def test_high_grade_passes() -> None:
    passed, score = StrongVerifier(_Grader("9")).verify("t", "a")
    assert passed is True and score == 0.9


def test_low_grade_fails() -> None:
    passed, score = StrongVerifier(_Grader("3")).verify("t", "a")
    assert passed is False and score == 0.3


def test_threshold_boundary() -> None:
    assert StrongVerifier(_Grader("6"), threshold=0.6).verify("t", "a")[0] is True
    assert StrongVerifier(_Grader("5"), threshold=0.6).verify("t", "a")[0] is False


def test_error_degrades_to_pass() -> None:
    class _Boom:
        def complete(self, messages: object, **kwargs: object) -> CompletionResult:
            raise RuntimeError("judge down")

    assert StrongVerifier(_Boom()).verify("t", "a") == (True, 1.0)  # fail-open


def test_parse_grade() -> None:
    assert _parse_grade("8") == 0.8 and _parse_grade("10/10") == 1.0
    assert _parse_grade("no number") == 1.0  # unparseable -> don't block


# --- integration: gated to hard turns ---------------------------------------------------


class _Worker:
    def __init__(self) -> None:
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer="my answer", steps=1, transcript=[], stopped_reason="done")


class _RejectThenApprove:
    """Manager rejects attempt 1, approves from attempt 2 (forces a hard, retried success)."""

    def __init__(self) -> None:
        self.calls = 0

    def review(self, task: str, answer: str, context: str) -> object:
        from chimera.core.supervisor import Review

        self.calls += 1
        return Review(approved=self.calls >= 2, feedback="not yet")


class _RecordingVerifier:
    def __init__(self, passes: bool) -> None:
        self.passes = passes
        self.calls = 0

    def verify(self, task: str, answer: str) -> tuple[bool, float]:
        self.calls += 1
        return (self.passes, 0.9 if self.passes else 0.2)


def test_strong_verify_only_fires_on_hard_turns() -> None:
    # Attempt 1 is rejected by the manager; attempt 2 is approved -> a HARD (retried) success.
    verifier = _RecordingVerifier(passes=True)
    agent = AutonomousAgent(
        _Worker(),
        manager=_RejectThenApprove(),
        strong_verifier=verifier,  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=3, use_planner=False, use_manager=True),
    )
    assert agent.run("hard task").success is True
    assert verifier.calls == 1  # graded only attempt 2 (index>1), never the failed attempt 1


def test_first_attempt_success_skips_strong_verify() -> None:
    verifier = _RecordingVerifier(passes=False)  # would reject if it ran
    agent = AutonomousAgent(
        _Worker(),
        strong_verifier=verifier,  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=3, use_planner=False, use_manager=False),
    )
    result = agent.run("easy task")
    assert result.success is True and verifier.calls == 0  # first pass isn't strong-verified


def test_strong_verify_rejection_fails_the_hard_success() -> None:
    verifier = _RecordingVerifier(passes=False)
    agent = AutonomousAgent(
        _Worker(),
        manager=_RejectThenApprove(),
        strong_verifier=verifier,  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=True),
    )
    result = agent.run("hard task")
    # Attempt 1 rejected by manager; attempt 2 approved but strong verifier rejects -> run fails.
    assert result.success is False
    assert "Independent verification" in result.attempts[1].feedback
