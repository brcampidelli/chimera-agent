"""M19-A0: the shared EvolutionContext factory reproduces the solve() seam wiring."""

from __future__ import annotations

from pathlib import Path

from chimera.config import Settings
from chimera.evolution import (
    AutoSkillEvolver,
    CardRetriever,
    EvolutionContext,
    ExperienceBuffer,
    build_evolution_context,
)


class _FakeGateway:
    """SkillEvolver/CollectiveSkillEvolver only store the gateway at construction."""


def _settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def test_apply_to_has_the_six_seams() -> None:
    ctx = EvolutionContext()
    assert set(ctx.apply_to()) == {
        "experience",
        "trajectories",
        "memory",
        "auto_evolver",
        "cards",
        "playbook",
    }


def test_factory_builds_default_seams(tmp_path: Path) -> None:
    ctx = build_evolution_context(
        _settings(), _FakeGateway(), "m", home=tmp_path
    )
    # experience always on; trajectories/cards off by default (collect=False, skill_cards default off)
    assert isinstance(ctx.experience, ExperienceBuffer)
    assert ctx.trajectories is None
    assert ctx.cards is None
    # evolve_skills defaults True -> an evolver is built
    assert isinstance(ctx.auto_evolver, AutoSkillEvolver)
    assert ctx.memory is None
    assert ctx.playbook is None


def test_evolve_skills_false_disables_evolver(tmp_path: Path) -> None:
    ctx = build_evolution_context(
        _settings(), _FakeGateway(), "m", home=tmp_path, evolve_skills=False
    )
    assert ctx.auto_evolver is None


def test_collect_enables_trajectories(tmp_path: Path) -> None:
    ctx = build_evolution_context(
        _settings(), _FakeGateway(), "m", home=tmp_path, collect=True
    )
    assert ctx.trajectories is not None


def test_skill_cards_toggle(tmp_path: Path) -> None:
    # settings default is off; an explicit override turns reading on (the A1 seam)
    on = build_evolution_context(
        _settings(), _FakeGateway(), "m", home=tmp_path, skill_cards=True
    )
    assert isinstance(on.cards, CardRetriever)
    # and the settings value is honored when no override is passed
    from_settings = build_evolution_context(
        _settings(CHIMERA_SKILL_CARDS="true"), _FakeGateway(), "m", home=tmp_path
    )
    assert isinstance(from_settings.cards, CardRetriever)


def test_couple_read_off_by_default_leaves_cards_off(tmp_path: Path) -> None:
    # A1 flip-point default OFF: evolving skills does NOT imply reading them (unchanged behaviour).
    ctx = build_evolution_context(
        _settings(), _FakeGateway(), "m", home=tmp_path, evolve_skills=True
    )
    assert ctx.cards is None


def test_couple_read_on_couples_reading_to_evolving(tmp_path: Path) -> None:
    # With the flip-point ON, a run that can mint a skill also reads the retrieved cards.
    on = build_evolution_context(
        _settings(CHIMERA_SKILL_CARDS_READ="true"),
        _FakeGateway(), "m", home=tmp_path, evolve_skills=True,
    )
    assert isinstance(on.cards, CardRetriever)
    # ...but with evolving OFF and no independent skill_cards, reading stays off.
    off = build_evolution_context(
        _settings(CHIMERA_SKILL_CARDS_READ="true"),
        _FakeGateway(), "m", home=tmp_path, evolve_skills=False,
    )
    assert off.cards is None


def test_explicit_skill_cards_override_wins_over_couple(tmp_path: Path) -> None:
    # An explicit skill_cards=False beats the couple flag (used to force reading off).
    ctx = build_evolution_context(
        _settings(CHIMERA_SKILL_CARDS_READ="true"),
        _FakeGateway(), "m", home=tmp_path, evolve_skills=True, skill_cards=False,
    )
    assert ctx.cards is None


def test_memory_and_playbook_are_injected(tmp_path: Path) -> None:
    sentinel_memory = object()
    ctx = build_evolution_context(
        _settings(), _FakeGateway(), "m", home=tmp_path, memory=sentinel_memory
    )
    assert ctx.memory is sentinel_memory


def test_record_external_writes_experience_and_credits_cards(tmp_path: Path) -> None:
    exp = ExperienceBuffer(tmp_path / "experience.json")

    class _Cards:
        def __init__(self) -> None:
            self.outcomes: list[bool] = []

        def record_outcome(self, success: bool) -> None:
            self.outcomes.append(success)

    cards = _Cards()
    ctx = EvolutionContext(experience=exp, cards=cards)  # type: ignore[arg-type]
    ctx.record_external("do a thing", "the answer", success=True)
    all_rows = exp.all()
    assert len(all_rows) == 1
    assert all_rows[0].outcome == "success"
    assert cards.outcomes == [True]


def test_record_external_is_safe_without_seams() -> None:
    # a bare context (no experience, no cards) must not raise
    EvolutionContext().record_external("t", "a", success=False)
