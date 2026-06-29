"""Tests for Tier-1 LLM skills and skill-context retrieval (no network)."""

from __future__ import annotations

from typing import Any

from chimera.providers import CompletionResult
from chimera.skills import (
    SkillRegistry,
    default_registry,
    retrieve_relevant_skills,
    skills_context_block,
)
from chimera.skills.builtin import CompleteCodeSkill, FixCodeSkill, GenerateScriptSkill


class FakeBackend:
    def __init__(self, content: str = "RESULT") -> None:
        self.content = content
        self.last_messages: list[Any] | None = None

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        self.last_messages = messages
        return CompletionResult(content=self.content, model="fake")


def test_complete_code_skill_uses_backend() -> None:
    backend = FakeBackend("    return a + b")
    skill = CompleteCodeSkill(backend)
    result = skill.execute(code="def add(a, b):", language="python")
    assert result.ok is True
    assert result.output == "    return a + b"
    assert backend.last_messages is not None
    assert skill.metrics.successes == 1


def test_fix_code_skill_requires_both_args() -> None:
    skill = FixCodeSkill(FakeBackend())
    result = skill.execute(code="x = 1")  # missing 'issue'
    assert result.ok is False
    assert "issue" in (result.error or "")


def test_generate_script_skill() -> None:
    backend = FakeBackend("print('hi')")
    result = GenerateScriptSkill(backend).execute(description="print hi", language="python")
    assert result.ok is True
    assert result.output == "print('hi')"


def test_default_registry_has_tier1_skills() -> None:
    registry = default_registry(backend=FakeBackend())
    for name in ("echo", "complete_code", "fix_code", "generate_script"):
        assert name in registry


def test_retrieval_ranks_relevant_skill_first() -> None:
    registry: SkillRegistry = default_registry(backend=FakeBackend())
    hits = retrieve_relevant_skills(registry, "fix a bug in my code", k=2)
    assert hits
    assert hits[0].name == "fix_code"
    block = skills_context_block(hits)
    assert "fix_code" in block


def test_retrieval_empty_query() -> None:
    registry = default_registry(backend=FakeBackend())
    assert retrieve_relevant_skills(registry, "") == []
