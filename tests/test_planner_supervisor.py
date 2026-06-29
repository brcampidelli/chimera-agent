"""Tests for the planner and the Worker-Manager supervisor (no network)."""

from __future__ import annotations

from typing import Any

from chimera.core.planner import Planner
from chimera.core.supervisor import Manager
from chimera.providers import CompletionResult


class FixedBackend:
    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content=self.content, model="fake")


def test_planner_parses_numbered_steps() -> None:
    backend = FixedBackend("1. Read the file\n2) Find the bug\n3. Fix it")
    plan = Planner(backend).plan("fix the bug")
    assert plan.steps == ["Read the file", "Find the bug", "Fix it"]
    assert "1. Read the file" in plan.as_text()


def test_planner_fallback_to_lines() -> None:
    backend = FixedBackend("do this\ndo that")
    plan = Planner(backend).plan("task")
    assert plan.steps == ["do this", "do that"]


def test_manager_approved() -> None:
    review = Manager(FixedBackend("APPROVED")).review("task", "result")
    assert review.approved is True
    assert review.feedback == ""


def test_manager_requests_revision() -> None:
    review = Manager(FixedBackend("REVISE: handle the empty input case")).review("task", "result")
    assert review.approved is False
    assert review.feedback == "handle the empty input case"


def test_manager_non_standard_reply_is_revision() -> None:
    review = Manager(FixedBackend("This is wrong because X")).review("task", "result")
    assert review.approved is False
    assert "wrong" in review.feedback
