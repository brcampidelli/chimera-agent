"""Self-evolving ecosystem (Tier 4): meta-agent, change-tempo governance, trajectories.

Agents that design agents (with isolation + hidden-test reward-hack defense),
governance of *change tempo* (not headcount), and trajectory collection that seeds
opt-in, external model-level evolution (LoRA/SFT/DPO).
"""

from chimera.ecosystem.change_queue import Change, ChangeQueue
from chimera.ecosystem.meta_agent import AgentBlueprint, MetaAgent, MetaEvalReport
from chimera.ecosystem.trajectory import Trajectory, TrajectoryCollector

__all__ = [
    "Change",
    "ChangeQueue",
    "AgentBlueprint",
    "MetaAgent",
    "MetaEvalReport",
    "Trajectory",
    "TrajectoryCollector",
]
