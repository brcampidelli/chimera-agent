"""Tests for the skill base, metrics and registry."""

from __future__ import annotations

import pytest

from chimera.skills import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillRegistry,
    default_registry,
)
from chimera.skills.builtin import EchoSkill


def test_echo_skill_success_records_metrics() -> None:
    skill = EchoSkill()
    result = skill.execute(text="hello")
    assert result.ok is True
    assert result.output == "hello"
    assert skill.metrics.runs == 1
    assert skill.metrics.successes == 1
    assert skill.metrics.success_rate == 1.0


def test_echo_skill_missing_arg_is_failure_not_crash() -> None:
    skill = EchoSkill()
    result = skill.execute()  # no 'text'
    assert result.ok is False
    assert result.error
    assert skill.metrics.failures == 1
    assert skill.metrics.success_rate == 0.0


def test_registry_register_and_duplicate() -> None:
    registry = SkillRegistry()
    registry.register(EchoSkill())
    assert "echo" in registry
    with pytest.raises(DuplicateSkillError):
        registry.register(EchoSkill())


def test_registry_unknown_skill_raises() -> None:
    registry = SkillRegistry()
    with pytest.raises(SkillNotFoundError):
        registry.get("missing")


def test_default_registry_loads_builtin() -> None:
    registry = default_registry()
    assert "echo" in registry
    assert len(registry) >= 1
