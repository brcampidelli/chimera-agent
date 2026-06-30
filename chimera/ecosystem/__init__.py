"""Self-evolving ecosystem (Tier 4): meta-agent, change-tempo governance, trajectories.

Agents that design agents (with isolation + hidden-test reward-hack defense),
governance of *change tempo* (not headcount), and trajectory collection that seeds
opt-in, external model-level evolution (LoRA/SFT/DPO).
"""

from chimera.ecosystem.change_queue import Change, ChangeQueue
from chimera.ecosystem.evolution import (
    CurationConfig,
    EvolutionReadiness,
    assess,
    curate_dpo,
    curate_sft,
    write_jsonl,
    write_recipe,
)
from chimera.ecosystem.meta_agent import AgentBlueprint, MetaAgent, MetaEvalReport
from chimera.ecosystem.spec import (
    AgentSpec,
    SearchStep,
    SpecSearchResult,
    model_proposer,
    search_spec,
)
from chimera.ecosystem.trajectory import Trajectory, TrajectoryCollector

__all__ = [
    "Change",
    "ChangeQueue",
    "AgentSpec",
    "SearchStep",
    "SpecSearchResult",
    "search_spec",
    "model_proposer",
    "AgentBlueprint",
    "MetaAgent",
    "MetaEvalReport",
    "Trajectory",
    "TrajectoryCollector",
    "CurationConfig",
    "EvolutionReadiness",
    "assess",
    "curate_sft",
    "curate_dpo",
    "write_jsonl",
    "write_recipe",
]
