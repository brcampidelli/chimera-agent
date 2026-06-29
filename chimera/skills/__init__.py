"""Skills — reusable procedures, both built-in and self-authored.

Each skill tracks metrics (success rate, latency) so the evolution engine can
refine the ones that are used often and retire the ones that underperform.
"""

from chimera.skills.base import Skill, SkillMetrics, SkillResult
from chimera.skills.registry import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillRegistry,
    default_registry,
)

__all__ = [
    "Skill",
    "SkillMetrics",
    "SkillResult",
    "SkillRegistry",
    "SkillNotFoundError",
    "DuplicateSkillError",
    "default_registry",
]
