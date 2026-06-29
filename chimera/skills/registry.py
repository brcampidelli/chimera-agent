"""A registry of skills available to an agent (built-in + self-authored)."""

from __future__ import annotations

from chimera.providers.gateway import SupportsComplete
from chimera.skills.base import Skill
from chimera.telemetry import get_logger

_log = get_logger("skills.registry")


class SkillNotFoundError(KeyError):
    """Raised when a skill is requested by an unregistered name."""


class DuplicateSkillError(ValueError):
    """Raised when registering a skill whose name is already taken."""


class SkillRegistry:
    """An ordered collection of uniquely-named skills."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill, *, replace: bool = False) -> None:
        if skill.name in self._skills and not replace:
            raise DuplicateSkillError(f"skill {skill.name!r} already registered")
        self._skills[skill.name] = skill
        _log.debug("registered skill %s v%s", skill.name, skill.version)

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillNotFoundError(name) from exc

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __len__(self) -> int:
        return len(self._skills)

    def names(self) -> list[str]:
        return list(self._skills)

    def skills(self) -> list[Skill]:
        return list(self._skills.values())


def default_registry(
    backend: SupportsComplete | None = None, model: str | None = None
) -> SkillRegistry:
    """Build a registry pre-loaded with the built-in skill library.

    LLM-backed skills use ``backend`` (or lazily build the default gateway).
    """
    from chimera.skills.builtin import register_builtin_skills

    registry = SkillRegistry()
    register_builtin_skills(registry, backend=backend, model=model)
    return registry
