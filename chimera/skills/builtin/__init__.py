"""Built-in skill library — ships ready to use, grows over milestones.

``register_builtin_skills`` is the single entry point the registry calls to load
every shipped skill.
"""

from __future__ import annotations

from chimera.skills.builtin.echo_skill import EchoSkill
from chimera.skills.registry import SkillRegistry


def register_builtin_skills(registry: SkillRegistry) -> None:
    """Register every built-in skill into ``registry``."""
    registry.register(EchoSkill())


__all__ = ["EchoSkill", "register_builtin_skills"]
