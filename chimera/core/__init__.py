"""Core agent loop + Tier-2 autonomy.

The minimal tool-calling loop (M1) plus the autonomous runner (M3): plan -> execute
-> Manager review -> verify-or-revert, with an experience buffer. State is kept
*outside* the LLM context (transcript + workspace snapshots) to resist
continuous-evolution degradation.
"""

from chimera.core.agent import (
    DEFAULT_SYSTEM_PROMPT,
    Agent,
    AgentConfig,
    AgentResult,
)
from chimera.core.autonomous import (
    Attempt,
    AutonomousAgent,
    AutonomousConfig,
    AutonomousResult,
    Worker,
)
from chimera.core.checkpoint import FileSnapshot, WorkspaceGuard
from chimera.core.contract import CompletionContract, ContractResult, parse_check
from chimera.core.events import AgentEvent, EventKind, EventSink
from chimera.core.explorer import (
    ContextExplorer,
    Evidence,
    ExploreRepositoryTool,
    ExplorerResult,
    parse_evidence,
)
from chimera.core.ledger import ProgressAssessment, ProgressLedger, TaskLedger
from chimera.core.planner import Plan, Planner
from chimera.core.spine import assemble_spine
from chimera.core.subagent import SubAgentTool
from chimera.core.supervisor import Manager, Review
from chimera.core.verify import (
    CommandVerifier,
    NullVerifier,
    VerificationResult,
    Verifier,
)
from chimera.providers import SupportsComplete

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentResult",
    "SupportsComplete",
    "DEFAULT_SYSTEM_PROMPT",
    "AutonomousAgent",
    "AutonomousConfig",
    "AutonomousResult",
    "Attempt",
    "Worker",
    "Planner",
    "Plan",
    "Manager",
    "Review",
    "WorkspaceGuard",
    "FileSnapshot",
    "ProgressLedger",
    "ProgressAssessment",
    "TaskLedger",
    "CompletionContract",
    "ContractResult",
    "parse_check",
    "AgentEvent",
    "EventKind",
    "EventSink",
    "Verifier",
    "VerificationResult",
    "CommandVerifier",
    "NullVerifier",
    "assemble_spine",
    "ContextExplorer",
    "ExplorerResult",
    "Evidence",
    "parse_evidence",
    "ExploreRepositoryTool",
    "SubAgentTool",
]
