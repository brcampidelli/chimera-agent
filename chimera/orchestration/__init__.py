"""Multi-agent orchestration: roles, supervisor/swarm, MOC communication.

Role specialization + shared memory + redundancy-consolidating communication. A
durable graph substrate (LangGraph) can back long-running crews in a later phase;
state is already externalized (memory + transcripts), so the core is substrate-agnostic.
"""

from chimera.orchestration.comms import AgentMessage, consolidate, render
from chimera.orchestration.crew import (
    CrewResult,
    IsolatedCrew,
    IsolatedCrewResult,
    IsolatedWorker,
    SequentialCrew,
    SupervisorCrew,
    demo_crew,
    parallel_review,
)
from chimera.orchestration.isolation import (
    IsolatedBatch,
    IsolatedResult,
    run_in_processes,
    run_isolated,
)
from chimera.orchestration.lifecycle import (
    LifecycleCrew,
    LifecycleResult,
    StageResult,
    lifecycle_crew,
)
from chimera.orchestration.roles import Role, RoleAgent

__all__ = [
    "Role",
    "RoleAgent",
    "AgentMessage",
    "consolidate",
    "render",
    "SequentialCrew",
    "SupervisorCrew",
    "CrewResult",
    "parallel_review",
    "demo_crew",
    "LifecycleCrew",
    "LifecycleResult",
    "StageResult",
    "lifecycle_crew",
    "run_isolated",
    "run_in_processes",
    "IsolatedResult",
    "IsolatedBatch",
    "IsolatedCrew",
    "IsolatedCrewResult",
    "IsolatedWorker",
]
