"""Self-evolution engine: multi-level (memory/skill/model), verify-or-revert.

Includes the experience buffer (failures as negative examples). The core of attacking
continuous-evolution degradation. The buffer ships in M3; the full engine in M4.
"""

from chimera.evolution.attribution import Fault, attribute, localize_fault, qualify
from chimera.evolution.auto_evolve import AutoSkillEvolver
from chimera.evolution.card_retrieval import CardIndex, CardRetriever, cards_context_block
from chimera.evolution.collective import CollectiveSkillEvolver
from chimera.evolution.context import EvolutionContext, build_evolution_context
from chimera.evolution.edit_diagnostic import EditClass, classify_edit, topology_key
from chimera.evolution.evolver import SkillEvolver
from chimera.evolution.experience import Experience, ExperienceBuffer, format_lessons
from chimera.evolution.gepa import (
    BackendExecutor,
    BackendReflector,
    Candidate,
    GEPAOptimizer,
    GEPAResult,
    TaskInstance,
    evolve_skill,
    optimize_template,
)
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.lifecycle_policy import LifecycleDecisions, SkillLifecyclePolicy
from chimera.evolution.playbook import (
    BackendDeltaProposer,
    Delta,
    Playbook,
    PlaybookCurator,
    PlaybookItem,
)
from chimera.evolution.refine_bridge import (
    RefineOutcome,
    instances_from_trajectories,
    refine_skill,
)
from chimera.evolution.rollback import RollbackDecision, apply_rollback, assess_rollback
from chimera.evolution.skill_nudges import SkillNudge, detect_skill_nudges
from chimera.evolution.skill_store import SkillStore
from chimera.evolution.stagnation import (
    StagnationDetector,
    StagnationReport,
    mean_pairwise_correlation,
    pearson,
)

__all__ = [
    "Experience",
    "ExperienceBuffer",
    "format_lessons",
    "LearnedSkill",
    "SkillStore",
    "SkillLifecyclePolicy",
    "LifecycleDecisions",
    "SkillEvolver",
    "GEPAOptimizer",
    "GEPAResult",
    "TaskInstance",
    "Candidate",
    "BackendExecutor",
    "BackendReflector",
    "optimize_template",
    "evolve_skill",
    "Playbook",
    "PlaybookItem",
    "PlaybookCurator",
    "BackendDeltaProposer",
    "Delta",
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
    "StagnationDetector",
    "StagnationReport",
    "pearson",
    "mean_pairwise_correlation",
    "EvolutionContext",
    "build_evolution_context",
    "RefineOutcome",
    "instances_from_trajectories",
    "refine_skill",
    "RollbackDecision",
    "assess_rollback",
    "apply_rollback",
]
