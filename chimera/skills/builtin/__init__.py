"""Built-in skill library — ships ready to use, grows over milestones.

``register_builtin_skills`` is the single entry point the registry calls to load
every shipped skill. LLM-backed skills receive the model backend (or lazily build
the default one).
"""

from __future__ import annotations

from chimera.providers.gateway import SupportsComplete
from chimera.skills.builtin.code_skills import (
    CompleteCodeSkill,
    FixCodeSkill,
    GenerateScriptSkill,
)
from chimera.skills.builtin.data_skills import DataAnalysisSkill, DataVisualizationSkill
from chimera.skills.builtin.echo_skill import EchoSkill
from chimera.skills.registry import SkillRegistry


def register_builtin_skills(
    registry: SkillRegistry,
    *,
    backend: SupportsComplete | None = None,
    model: str | None = None,
) -> None:
    """Register every built-in skill into ``registry``."""
    registry.register(EchoSkill())
    registry.register(CompleteCodeSkill(backend, model))
    registry.register(FixCodeSkill(backend, model))
    registry.register(GenerateScriptSkill(backend, model))
    registry.register(DataAnalysisSkill(backend, model))
    registry.register(DataVisualizationSkill(backend, model))


__all__ = [
    "CompleteCodeSkill",
    "DataAnalysisSkill",
    "DataVisualizationSkill",
    "EchoSkill",
    "FixCodeSkill",
    "GenerateScriptSkill",
    "register_builtin_skills",
]
