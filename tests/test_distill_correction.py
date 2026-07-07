"""Tests for distilling a verified failed→passed correction into a skill card (M15-B4)."""

from __future__ import annotations

from pathlib import Path

from chimera.evolution.auto_evolve import AutoSkillEvolver
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.skill_store import SkillStore
from chimera.providers.gateway import CompletionResult, Message


class _FakeBackend:
    """Returns a canned JSON correction card, and records what it was asked."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.seen: list[str] = []

    def complete(self, messages: list[Message], **kwargs: object) -> CompletionResult:
        self.seen.append(messages[-1].content)
        return CompletionResult(content=self.content, model="fake")


_GOOD_CARD = (
    '{"name": "handle_empty_input", "description": "guard empty input", '
    '"trigger": "parsing user input", "do": "check for empty string before indexing", '
    '"avoid": "indexing [0] without a length check", "check": "empty input returns a clear error", '
    '"risk": "off-by-one on boundary inputs", "triggers": ["empty", "input", "guard", "boundary"]}'
)


def test_distill_extracts_the_fix_from_the_pair() -> None:
    backend = _FakeBackend(_GOOD_CARD)
    evolver = SkillEvolver(backend)
    card = evolver.distill_correction(
        "parse the config", failed="crashed on empty input", passed="guards empty input first"
    )
    assert card is not None
    assert card.kind == "anti_pattern"
    assert card.do and card.check  # a usable card carries both a fix and a way to verify it
    # The prompt carried BOTH attempts — the eval-supplied signal, no human needed.
    prompt = backend.seen[-1]
    assert "FAILED attempt" in prompt and "PASSED attempt" in prompt


def test_distill_rejects_a_card_without_do_or_check() -> None:
    thin = '{"name": "x", "description": "d", "do": "", "check": ""}'
    assert SkillEvolver(_FakeBackend(thin)).distill_correction("t", "f", "p") is None


def test_distill_rejects_unparseable() -> None:
    assert SkillEvolver(_FakeBackend("not json at all")).distill_correction("t", "f", "p") is None


def test_auto_distill_stores_the_card(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    auto = AutoSkillEvolver(SkillEvolver(_FakeBackend(_GOOD_CARD)), store)
    card = auto.maybe_distill_correction("parse the config", "crashed", "guarded")
    assert card is not None and "handle_empty_input" in store


def test_auto_distill_tainted_card_is_held_pending(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    auto = AutoSkillEvolver(SkillEvolver(_FakeBackend(_GOOD_CARD)), store)
    card = auto.maybe_distill_correction("t", "f", "p", tainted=True)
    assert card is not None and card.status == "pending" and card.provenance == "tainted"


def test_auto_distill_skips_duplicate(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    auto = AutoSkillEvolver(SkillEvolver(_FakeBackend(_GOOD_CARD)), store)
    assert auto.maybe_distill_correction("t", "f", "p") is not None
    assert auto.maybe_distill_correction("t", "f", "p") is None  # already stored


def test_autonomous_distills_after_a_recovered_failure(tmp_path: Path) -> None:
    """End-to-end: a task that fails then passes distills a correction card from the pair."""
    from chimera.core.agent import AgentResult
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
    from chimera.core.supervisor import Review

    class _FlakyWorker:
        def __init__(self) -> None:
            self.n = 0

        def run(self, task: str) -> AgentResult:
            self.n += 1
            return AgentResult(answer=f"attempt-{self.n}", steps=1, stopped_reason="final")

    # Manager approves only the 2nd attempt → attempt 1 fails, attempt 2 passes.
    class _Manager:
        def __init__(self) -> None:
            self.n = 0

        def review(self, task: str, proposed: str, *, context: str = "") -> Review:
            self.n += 1
            return Review(approved=self.n >= 2, feedback="" if self.n >= 2 else "try again")

    store = SkillStore(tmp_path / "skills.json")
    auto_evolver = AutoSkillEvolver(SkillEvolver(_FakeBackend(_GOOD_CARD)), store)
    auto = AutonomousAgent(
        _FlakyWorker(), manager=_Manager(), auto_evolver=auto_evolver,
        config=AutonomousConfig(use_planner=False, use_manager=True, max_attempts=3),
    )
    result = auto.run("parse the config")
    assert result.success is True
    # The failed→passed correction was distilled and stored.
    assert "handle_empty_input" in store
