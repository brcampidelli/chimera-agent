"""Tests for the requirement checklist (M14 B1) — extract + coverage-grade the task."""

from __future__ import annotations

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.checklist import Requirement, RequirementChecklist
from chimera.providers.gateway import CompletionResult


class _Backend:
    """Returns queued canned completions in order (for extract then grade calls)."""

    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls = 0

    def complete(self, messages: object, **kwargs: object) -> CompletionResult:
        self.calls += 1
        content = self.contents.pop(0) if self.contents else "{}"
        return CompletionResult(content=content, model="fake")


# --- extract / grade --------------------------------------------------------------------


def test_extract_parses_requirements() -> None:
    backend = _Backend(['{"items": [{"text": "return JSON", "kind": "include"}, {"text": "no prints", "kind": "avoid"}]}'])
    items = RequirementChecklist(backend).extract("do the thing")
    assert [r.text for r in items] == ["return JSON", "no prints"]
    assert items[1].kind == "avoid"


def test_extract_neutral_on_bad_json() -> None:
    assert RequirementChecklist(_Backend(["not json"])).extract("t") == []


def test_grade_returns_only_misses() -> None:
    reqs = [Requirement(text="A"), Requirement(text="B"), Requirement(text="C")]
    backend = _Backend(['{"items": [{"text":"A","met":true},{"text":"B","met":false},{"text":"C","met":true}]}'])
    misses = RequirementChecklist(backend).grade("t", "answer", reqs)
    assert misses == ["B"]


def test_grade_empty_when_no_requirements() -> None:
    backend = _Backend([])
    assert RequirementChecklist(backend).grade("t", "a", []) == []
    assert backend.calls == 0  # no model call when there's nothing to grade


def test_grade_neutral_on_error() -> None:
    reqs = [Requirement(text="A")]
    assert RequirementChecklist(_Backend(["garbage"])).grade("t", "a", reqs) == []  # no false miss


# --- integration with the solve loop ----------------------------------------------------


class _OkWorker:
    def __init__(self) -> None:
        self.runs = 0

    def run(self, task: str) -> AgentResult:
        self.runs += 1
        return AgentResult(answer="did it", steps=1, transcript=[], stopped_reason="done")


class _RecordingChecklist:
    """A stand-in that reports fixed misses on attempt 1, then none — drives a targeted retry."""

    def __init__(self) -> None:
        self.graded = 0

    def extract(self, task: str) -> list[Requirement]:
        return [Requirement(text="include the error code")]

    def grade(self, task: str, answer: str, requirements: list[Requirement]) -> list[str]:
        self.graded += 1
        return ["include the error code"] if self.graded == 1 else []


def test_missed_requirement_fails_attempt_and_retries() -> None:
    worker = _OkWorker()
    agent = AutonomousAgent(
        worker,
        checklist=_RecordingChecklist(),  # type: ignore[arg-type]
        config=AutonomousConfig(max_attempts=2, use_planner=False, use_manager=False),
    )
    result = agent.run("write the handler")
    assert result.success is True  # attempt 2 covers the requirement
    assert worker.runs == 2  # attempt 1 failed the coverage gate, forcing a retry
    assert "include the error code" in result.attempts[0].feedback


def test_no_checklist_is_unaffected() -> None:
    worker = _OkWorker()
    agent = AutonomousAgent(
        worker, config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False)
    )
    assert agent.run("t").success is True and worker.runs == 1
