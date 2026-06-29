"""The skill-evolution loop: propose -> test -> keep/discard, and refine.

When the agent succeeds at a task, the evolver asks a model to generalize it into a
reusable :class:`LearnedSkill`, then **tests** that skill before keeping it — the same
verify-or-revert discipline as the autonomous loop, applied to the agent's own skills.
Refinement improves a skill's template from its failure examples (continuous learning).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from chimera.evolution.learned_skill import LearnedSkill
from chimera.providers.gateway import Message, SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("evolution.evolver")

_PROPOSE_SYSTEM = (
    "You convert a successfully completed task into a REUSABLE skill. Reply with ONLY "
    'a JSON object: {"name": "snake_case_name", "description": "one line", '
    '"prompt_template": "a reusable instruction with {placeholder} variables for inputs"}.'
)
_REFINE_SYSTEM = (
    "Improve a skill's prompt_template given examples of how it failed. Reply with ONLY "
    'a JSON object: {"prompt_template": "the improved template"}.'
)
_JSON = re.compile(r"\{.*\}", re.DOTALL)


def _parse_json(text: str) -> dict[str, str] | None:
    match = _JSON.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _bump(version: str) -> str:
    parts = version.split(".")
    try:
        parts[-1] = str(int(parts[-1]) + 1)
    except (ValueError, IndexError):
        return version
    return ".".join(parts)


class SkillEvolver:
    """Proposes, tests and refines learned skills with a model backend."""

    def __init__(self, backend: SupportsComplete, model: str | None = None) -> None:
        self.backend = backend
        self.model = model

    def propose(self, task: str, solution: str) -> LearnedSkill | None:
        user = f"Task:\n{task}\n\nSuccessful approach/solution:\n{solution}"
        raw = self.backend.complete(
            [Message(role="system", content=_PROPOSE_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if not data or not all(k in data for k in ("name", "description", "prompt_template")):
            _log.debug("proposal could not be parsed")
            return None
        return LearnedSkill(
            name=str(data["name"]),
            description=str(data["description"]),
            prompt_template=str(data["prompt_template"]),
            backend=self.backend,
            model=self.model,
        )

    def test_skill(
        self,
        skill: LearnedSkill,
        test_input: dict[str, str],
        check: Callable[[str], bool],
    ) -> bool:
        result = skill.execute(**test_input)
        return result.ok and check(result.output)

    def evolve(
        self,
        task: str,
        solution: str,
        *,
        test_input: dict[str, str],
        check: Callable[[str], bool],
    ) -> LearnedSkill | None:
        """Propose a skill and keep it only if it passes the test; else discard."""
        skill = self.propose(task, solution)
        if skill is None:
            return None
        if self.test_skill(skill, test_input, check):
            _log.debug("kept learned skill %s", skill.name)
            return skill
        _log.debug("discarded learned skill %s (failed test)", skill.name)
        return None

    def refine(self, skill: LearnedSkill, failures: list[str]) -> LearnedSkill:
        user = "Template:\n" + skill.prompt_template + "\n\nFailures:\n" + "\n".join(failures)
        raw = self.backend.complete(
            [Message(role="system", content=_REFINE_SYSTEM), Message(role="user", content=user)],
            model=self.model,
            temperature=0.2,
        ).content
        data = _parse_json(raw)
        if data and "prompt_template" in data:
            return LearnedSkill(
                name=skill.name,
                description=skill.description,
                prompt_template=str(data["prompt_template"]),
                version=_bump(skill.version),
                backend=self.backend,
                model=self.model,
            )
        return skill
