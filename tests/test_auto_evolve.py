"""Tests for the auto-skill-evolution hook (recurrence -> govern -> smoke-test -> store)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from chimera.evolution import AutoSkillEvolver, LearnedSkill, SkillEvolver, SkillStore
from chimera.governance.validator import SkillValidator
from chimera.providers import CompletionResult

PROPOSAL = (
    '{"name": "summarize_text", "description": "summarize text", '
    '"prompt_template": "Summarize {text}."}'
)
ANTI_PATTERN = (
    '{"name": "off_by_one", "description": "fencepost error in loop bounds", '
    '"trigger": "iterating with an index", "do": "iterate 0..n-1", '
    '"avoid": "iterating 0..n", "check": "index never equals the length", '
    '"risk": "empty collections", "triggers": ["loop", "index", "bound"]}'
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


def test_evolves_anti_pattern_card_on_recurring_failure(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    auto = _auto(ScriptedBackend([ANTI_PATTERN]), store, validator=SkillValidator())
    card = auto.maybe_evolve_failure("loop task", "off by one again", prior_failures=2)
    assert card is not None and card.kind == "anti_pattern" and card.name == "off_by_one"
    assert card.do and card.check  # the corrective content the injection will surface
    assert "off_by_one" in store


def test_no_anti_pattern_below_recurrence(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    auto = _auto(ScriptedBackend([ANTI_PATTERN]), store)
    assert auto.maybe_evolve_failure("t", "d", prior_failures=1) is None
    assert len(store) == 0  # a one-off failure does not spawn a card


def test_anti_pattern_missing_check_is_discarded(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    no_check = '{"name": "x_bad", "description": "d", "do": "something", "avoid": "y"}'
    auto = _auto(ScriptedBackend([no_check]), store, validator=SkillValidator())
    assert auto.maybe_evolve_failure("t", "d", prior_failures=2) is None
    assert len(store) == 0  # TRS rule: an anti-pattern card needs Do + Check


class FakeCollective:
    """A collective evolver stub with a fixed candidate and a fixed (passed, n) transfer."""

    def __init__(self, candidate: LearnedSkill, counts: tuple[int, int]) -> None:
        self.candidate = candidate
        self.counts = counts

    def propose_collective(self, task: str, solution: str) -> list[LearnedSkill]:
        return [self.candidate]

    def transfer_counts(self, skill: Any, test_input: Any, check: Any) -> tuple[int, int]:
        return self.counts

    def transferability(self, skill: Any, test_input: Any, check: Any) -> float:
        passed, n = self.counts
        return passed / n if n else 0.0


def _collective_auto(counts: tuple[int, int], mode: str, store: SkillStore) -> AutoSkillEvolver:
    candidate = LearnedSkill(name="lucky_skill", description="d", prompt_template="do {x}")
    return AutoSkillEvolver(
        SkillEvolver(ScriptedBackend([])),
        store,
        collective=FakeCollective(candidate, counts),  # type: ignore[arg-type]
        min_recurrences=2,
        accept_mode=mode,
    )


def test_point_mode_accepts_lucky_two_of_three(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    # frac 2/3 = 0.67 >= 0.5 -> accepted under the raw point estimate
    assert _collective_auto((2, 3), "point", store).maybe_evolve("t", "s", 2) is not None
    assert "lucky_skill" in store


def test_wilson_mode_rejects_lucky_two_of_three(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "s.json")
    # Wilson lower bound of 2/3 is ~0.21 < 0.5 -> rejected as small-sample luck
    assert _collective_auto((2, 3), "wilson", store).maybe_evolve("t", "s", 2) is None
    assert len(store) == 0
