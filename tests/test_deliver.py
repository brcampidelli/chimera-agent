"""Tests for Deliverable Mode (no network)."""

from __future__ import annotations

from typing import Any

from chimera.deliver import deliverable_system_prompt, produce_deliverable
from chimera.providers import CompletionResult


class CannedBackend:
    def __init__(self, content: str) -> None:
        self.content = content
        self.seen: list[Any] = []

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.seen = messages
        return CompletionResult(content=self.content, model="fake")


def test_produce_deliverable_returns_the_document() -> None:
    assert produce_deliverable(CannedBackend("# Report\n\nbody"), "make a report").startswith("# Report")


def test_system_prompt_reflects_the_format() -> None:
    assert "HTML" in deliverable_system_prompt("html")
    assert "Markdown" in deliverable_system_prompt("md")


def test_request_is_passed_as_the_user_message() -> None:
    backend = CannedBackend("doc")
    produce_deliverable(backend, "build a spec", fmt="txt")
    assert backend.seen[0].content == deliverable_system_prompt("txt")
    assert backend.seen[1].content == "build a spec"
