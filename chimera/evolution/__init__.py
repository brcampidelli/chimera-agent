"""Self-evolution engine: multi-level (memory/skill/model), verify-or-revert.

Includes the experience buffer (failures as negative examples). The core of attacking
continuous-evolution degradation. The buffer ships in M3; the full engine in M4.
"""

from chimera.evolution.attribution import Fault, attribute, localize_fault, qualify
from chimera.evolution.auto_evolve import AutoSkillEvolver
from chimera.evolution.card_retrieval import CardIndex, CardRetriever, cards_context_block
from chimera.evolution.collective import CollectiveSkillEvolver
from chimera.evolution.edit_diagnostic import EditClass, classify_edit, topology_key
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.experience import Experience, ExperienceBuffer, format_lessons
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_nudges import SkillNudge, detect_skill_nudges
from chimera.evolution.skill_store import SkillStore

__all__ = [
    "Experience",
    "ExperienceBuffer",
    "format_lessons",
    "LearnedSkill",
    "SkillStore",
    "SkillEvolver",
    "AutoSkillEvolver",
    "CardIndex",
    "CardRetriever",
    "cards_context_block",
    "classify_edit",
    "topology_key",
    "EditClass",
    "CollectiveSkillEvolver",
    "SkillNudge",
    "detect_skill_nudges",
    "Fault",
    "localize_fault",
    "attribute",
    "qualify",
]
