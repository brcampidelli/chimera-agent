"""Tests for the progress ledger (M13 B1) — structured per-attempt self-check."""

from __future__ import annotations

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.ledger import ProgressAssessment, ProgressLedger
from chimera.providers.gateway import CompletionResult


class _Backend:
    """A backend whose completion returns a canned string (the ledger's JSON)."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.content, model="fake")


def test_assess_parses_valid_json() -> None:
    backend = _Backend('{"complete": false, "progressing": true, "next_focus": "check the API path"}')
    a = ProgressLedger(backend).assess("t", "ans", "failed", attempt=1, max_attempts=3)
    assert a == ProgressAssessment(complete=False, progressing=True, next_focus="check the API path")


def test_assess_strips_code_fence() -> None:
    backend = _Backend('```json\n{"complete": true, "progressing": true, "next_focus": ""}\n```')
    a = ProgressLedger(backend).assess("t", "ans", "", attempt=2, max_attempts=3)
    assert a.complete is True and a.next_focus == ""


def test_assess_neutral_on_malformed() -> None:
    a = ProgressLedger(_Backend("not json at all")).assess("t", "a", "f", attempt=1, max_attempts=2)
    assert a == ProgressAssessment(complete=False, progressing=True, next_focus="")


def test_assess_neutral_on_backend_error() -> None:
    class _Boom:
        def complete(self, messages: object, **kwargs: object) -> CompletionResult:
            raise RuntimeError("model down")

    a = ProgressLedger(_Boom()).assess("t", "a", "f", attempt=1, max_attempts=2)
    assert a.progressing is True and a.next_focus == ""  # never breaks the loop


# --- integration with the solve loop ----------------------------------------------------


class _FailingWorker:
    """A worker that always fails and records every prompt it is handed."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, task: str) -> AgentResult:
        self.prompts.append(task)
        return AgentResult(answer="nope", steps=1, transcript=[], stopped_reason="done")


class _RecordingLedger:
    """A stand-in ProgressLedger that records calls and returns a fixed assessment."""

    def __init__(self, assessment: ProgressAssessment) -> None:
        self.assessment = assessment
        self.seen: list[int] = []

    def assess(self, task: str, answer: str, feedback: str, *, attempt: int, max_attempts: int) -> ProgressAssessment:
        self.seen.append(attempt)
        return self.assessment


class _AlwaysReject:
    """A Manager-like reviewer that always fails the attempt."""

    def review(self, task: str, answer: str, context: str) -> object:
        from chimera.core.supervisor import Review

        return Review(approved=False, feedback="not good enough")


def test_ledger_next_focus_injected_into_retry() -> None:
    worker = _FailingWorker()
    ledger = _RecordingLedger(ProgressAssessment(complete=False, progressing=True, next_focus="use the cache"))
    agent = AutonomousAgent(
        worker,
        manager=_AlwaysReject(),
        progress_ledger=ledger,  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=True),
    )
    result = agent.run("do the thing")
    assert result.success is False
    assert ledger.seen == [1, 2]  # assessed after each failed attempt
    # The next_focus from attempt 1's assessment must reach attempt 2's actual prompt.
    assert len(worker.prompts) == 2
    assert "use the cache" in worker.prompts[1]
    assert "use the cache" not in worker.prompts[0]  # not present before the ledger ran
