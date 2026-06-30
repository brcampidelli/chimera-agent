"""Tests for the auto-skill-evolution hook (recurrence -> govern -> smoke-test -> store)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.evolution import AutoSkillEvolver, SkillEvolver, SkillStore
from chimera.governance.validator import SkillValidator
from chimera.providers import CompletionResult

PROPOSAL = (
    '{"name": "summarize_text", "description": "summarize text", '
    '"prompt_template": "Summarize {text}."}'
)


class ScriptedBackend:
    def __init__(self, contents: list[str]) -> None:
        self._contents = list(contents)

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        content = self._contents.pop(0) if self._contents else ""
        return CompletionResult(content=content, model="fake")


def _auto(backend: Any, store: SkillStore, validator: Any = None, min_recurrences: int = 2) -> AutoSkillEvolver:
    return AutoSkillEvolver(
        SkillEvolver(backend), store, validator=validator, min_recurrences=min_recurrences
    )


def test_does_not_evolve_below_recurrence_threshold(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    auto = _auto(ScriptedBackend([PROPOSAL, "out"]), store)
    assert auto.maybe_evolve("task", "solution", prior_successes=1) is None
    assert len(store) == 0


def test_evolves_keeps_and_stores_when_recurring(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    auto = _auto(ScriptedBackend([PROPOSAL, "a real summary"]), store, validator=SkillValidator())
    skill = auto.maybe_evolve("summarize this doc", "did it", prior_successes=2)
    assert skill is not None and skill.name == "summarize_text"
    assert "summarize_text" in store


def test_rejected_by_governance_is_not_stored(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    reject = SimpleNamespace(validate=lambda data: SimpleNamespace(accepted=False))
    auto = _auto(ScriptedBackend([PROPOSAL]), store, validator=reject)
    assert auto.maybe_evolve("t", "s", prior_successes=3) is None
    assert len(store) == 0  # governance gate rejected it before it ran


def test_failed_smoke_test_is_not_stored(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    auto = _auto(ScriptedBackend([PROPOSAL, ""]), store)  # empty output fails the smoke test
    assert auto.maybe_evolve("t", "s", prior_successes=2) is None
    assert len(store) == 0


def test_existing_skill_is_not_duplicated(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    assert _auto(ScriptedBackend([PROPOSAL, "out"]), store).maybe_evolve("t", "s", 2) is not None
    # a second proposal of the same name is skipped
    assert _auto(ScriptedBackend([PROPOSAL, "out"]), store).maybe_evolve("t", "s", 2) is None
    assert len(store) == 1
