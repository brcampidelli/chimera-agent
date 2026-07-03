"""Tests for learned skills, the skill store, and the skill evolver (no network)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.evolution import LearnedSkill, SkillEvolver, SkillStore
from chimera.providers import CompletionResult

_PROPOSAL = (
    '{"name": "greet_person", "description": "Greet a person warmly", '
    '"prompt_template": "Write a one-line friendly greeting to {name}."}'
)


class RoutingBackend:
    """Routes by the system prompt: proposals vs refinement vs skill execution."""

    def __init__(self, *, proposal: str = _PROPOSAL, skill_output: str = "OUTPUT", refine: str = "") -> None:
        self.proposal = proposal
        self.skill_output = skill_output
        self.refine = refine

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        system = ""
        for message in messages:
            data = message.as_dict() if hasattr(message, "as_dict") else message
            if data.get("role") == "system":
                system = str(data.get("content", ""))
                break
        if "REUSABLE skill" in system:
            content = self.proposal
        elif "Improve a skill" in system:
            content = self.refine
        else:
            content = self.skill_output
        return CompletionResult(content=content, model="fake")


def test_learned_skill_runs_template() -> None:
    skill = LearnedSkill(
        name="greet",
        description="d",
        prompt_template="Greet {name}.",
        backend=RoutingBackend(skill_output="Hi Alex!"),
    )
    result = skill.execute(name="Alex")
    assert result.ok is True
    assert result.output == "Hi Alex!"


def test_learned_skill_missing_variable() -> None:
    skill = LearnedSkill(name="g", description="d", prompt_template="Greet {name}.", backend=RoutingBackend())
    result = skill.execute()  # no 'name'
    assert result.ok is False
    assert "missing template variable" in (result.error or "")


def test_evolver_proposes_skill() -> None:
    skill = SkillEvolver(RoutingBackend()).propose("greet someone", "I said hello")
    assert skill is not None
    assert skill.name == "greet_person"
    assert "{name}" in skill.prompt_template


_CARD_PROPOSAL = (
    '{"name": "greet_person", "description": "greet", "prompt_template": "Greet {name}.", '
    '"trigger": "meeting someone", "do": "say hi by name", "avoid": "being cold", '
    '"check": "the name is included", "risk": "wrong name", "triggers": ["greet", "hello"]}'
)


def test_propose_populates_card_fields() -> None:
    skill = SkillEvolver(RoutingBackend(proposal=_CARD_PROPOSAL)).propose("greet", "hi")
    assert skill is not None
    assert skill.do == "say hi by name"
    assert skill.check == "the name is included"
    assert skill.triggers == ["greet", "hello"]
    assert skill.kind == "pattern"


class _FixedBackend:
    """Returns the same content for any call (routing by system prompt not needed here)."""

    def __init__(self, content: str) -> None:
        self.content = content

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content=self.content, model="fake")


def test_propose_failure_card_is_anti_pattern() -> None:
    anti = (
        '{"name": "off_by_one", "description": "fencepost", "do": "iterate 0..n-1", '
        '"avoid": "0..n", "check": "index < length", "triggers": ["loop"]}'
    )
    card = SkillEvolver(_FixedBackend(anti)).propose_failure_card("loop", "off by one")
    assert card is not None
    assert card.kind == "anti_pattern"
    assert card.prompt_template == ""  # advisory: not executable
    assert card.do and card.check


def test_evolve_keeps_passing_skill() -> None:
    evolver = SkillEvolver(RoutingBackend(skill_output="OUTPUT"))
    kept = evolver.evolve(
        "task", "solution", test_input={"name": "x"}, check=lambda out: out == "OUTPUT"
    )
    assert kept is not None


def test_evolve_discards_failing_skill() -> None:
    evolver = SkillEvolver(RoutingBackend(skill_output="WRONG"))
    kept = evolver.evolve(
        "task", "solution", test_input={"name": "x"}, check=lambda out: out == "OUTPUT"
    )
    assert kept is None


def test_evolver_handles_unparseable_proposal() -> None:
    assert SkillEvolver(RoutingBackend(proposal="not json")).propose("t", "s") is None


def test_refine_bumps_version() -> None:
    skill = LearnedSkill(name="g", description="d", prompt_template="old", version="0.1.0")
    refine_json = '{"prompt_template": "new and better {x}"}'
    refined = SkillEvolver(RoutingBackend(refine=refine_json)).refine(skill, ["failed on empty"])
    assert refined.prompt_template == "new and better {x}"
    assert refined.version == "0.1.1"


def test_skill_store_roundtrip(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills.json")
    store.add(LearnedSkill(name="greet", description="d", prompt_template="Greet {name}."))
    assert "greet" in store

    reopened = SkillStore(tmp_path / "skills.json")
    backend = RoutingBackend(skill_output="Hello!")
    skills = reopened.skills(backend=backend)
    assert len(skills) == 1
    assert skills[0].execute(name="Alex").output == "Hello!"
