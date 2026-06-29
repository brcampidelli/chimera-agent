"""Skills — reusable procedures, both built-in and self-authored.

Each skill tracks metrics (success rate, latency) so the evolution engine can
refine the ones that are used often and retire the ones that underperform.
"""

from chimera.skills.base import Skill, SkillMetrics, SkillResult
from chimera.skills.llm_skill import LLMSkill
from chimera.skills.registry import (
    DuplicateSkillError,
    SkillNotFoundError,
    SkillRegistry,
    default_registry,
)
from chimera.skills.retrieval import retrieve_relevant_skills, skills_context_block

__all__ = [
    "Skill",
    "SkillMetrics",
    "SkillResult",
    "LLMSkill",
    "SkillRegistry",
    "SkillNotFoundError",
    "DuplicateSkillError",
    "default_registry",
    "retrieve_relevant_skills",
    "skills_context_block",
]
