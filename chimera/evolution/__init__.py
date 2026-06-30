"""Self-evolution engine: multi-level (memory/skill/model), verify-or-revert.

Includes the experience buffer (failures as negative examples). The core of attacking
continuous-evolution degradation. The buffer ships in M3; the full engine in M4.
"""

from chimera.evolution.auto_evolve import AutoSkillEvolver
from chimera.evolution.collective import CollectiveSkillEvolver
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.experience import Experience, ExperienceBuffer, format_lessons
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore

__all__ = [
    "Experience",
    "ExperienceBuffer",
    "format_lessons",
    "LearnedSkill",
    "SkillStore",
    "SkillEvolver",
    "AutoSkillEvolver",
    "CollectiveSkillEvolver",
]
