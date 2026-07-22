"""Core agent loop + Tier-2 autonomy.

The minimal tool-calling loop (M1) plus the autonomous runner (M3): plan -> execute
-> Manager review -> verify-or-revert, with an experience buffer. State is kept
*outside* the LLM context (transcript + workspace snapshots) to resist
continuous-evolution degradation.

**Why the re-exports are lazy.** See :mod:`chimera.eval`. This package's ``__init__`` is the one that
cost the desktop sidecar most: importing ``chimera.core.agent`` — which the chat endpoint genuinely
needs, and whose own imports are cheap — ran this ``__init__`` and so also pulled ``autonomous`` →
``chimera.ecosystem`` → ``meta_agent`` → ``chimera.evolution.experience``, none of which a chat turn
touches. Names resolve on first access (PEP 562) and are cached; ``from chimera.core import Agent``
still works, and the ``TYPE_CHECKING`` block keeps mypy's view exact.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
    from chimera.core.checklist import RequirementChecklist
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
    from chimera.core.repomap import build_repo_map
    from chimera.core.runstate import RunCheckpointer
    from chimera.core.spec_test import (
        SpecTestGenerator,
        SpecTestVerifier,
        workspace_digest,
    )
    from chimera.core.spine import assemble_spine
    from chimera.core.strong_verify import StrongVerifier
    from chimera.core.subagent import SubAgentTool
    from chimera.core.supervisor import Manager, Review
    from chimera.core.verify import (
        CommandVerifier,
        NullVerifier,
        VerificationResult,
        Verifier,
    )
    from chimera.providers import SupportsComplete

# Exported name -> (module path relative to this package, attribute). ``SupportsComplete`` is the one
# entry that lives outside `chimera.core`, so it carries an absolute path (leading dot-free marker).
_LAZY: dict[str, tuple[str, str]] = {
    "Agent": ("agent", "Agent"),
    "AgentConfig": ("agent", "AgentConfig"),
    "AgentResult": ("agent", "AgentResult"),
    "DEFAULT_SYSTEM_PROMPT": ("agent", "DEFAULT_SYSTEM_PROMPT"),
    "Attempt": ("autonomous", "Attempt"),
    "AutonomousAgent": ("autonomous", "AutonomousAgent"),
    "AutonomousConfig": ("autonomous", "AutonomousConfig"),
    "AutonomousResult": ("autonomous", "AutonomousResult"),
    "Worker": ("autonomous", "Worker"),
    "RequirementChecklist": ("checklist", "RequirementChecklist"),
    "FileSnapshot": ("checkpoint", "FileSnapshot"),
    "WorkspaceGuard": ("checkpoint", "WorkspaceGuard"),
    "CompletionContract": ("contract", "CompletionContract"),
    "ContractResult": ("contract", "ContractResult"),
    "parse_check": ("contract", "parse_check"),
    "AgentEvent": ("events", "AgentEvent"),
    "EventKind": ("events", "EventKind"),
    "EventSink": ("events", "EventSink"),
    "ContextExplorer": ("explorer", "ContextExplorer"),
    "Evidence": ("explorer", "Evidence"),
    "ExploreRepositoryTool": ("explorer", "ExploreRepositoryTool"),
    "ExplorerResult": ("explorer", "ExplorerResult"),
    "parse_evidence": ("explorer", "parse_evidence"),
    "ProgressAssessment": ("ledger", "ProgressAssessment"),
    "ProgressLedger": ("ledger", "ProgressLedger"),
    "TaskLedger": ("ledger", "TaskLedger"),
    "Plan": ("planner", "Plan"),
    "Planner": ("planner", "Planner"),
    "build_repo_map": ("repomap", "build_repo_map"),
    "RunCheckpointer": ("runstate", "RunCheckpointer"),
    "SpecTestGenerator": ("spec_test", "SpecTestGenerator"),
    "SpecTestVerifier": ("spec_test", "SpecTestVerifier"),
    "workspace_digest": ("spec_test", "workspace_digest"),
    "assemble_spine": ("spine", "assemble_spine"),
    "StrongVerifier": ("strong_verify", "StrongVerifier"),
    "SubAgentTool": ("subagent", "SubAgentTool"),
    "Manager": ("supervisor", "Manager"),
    "Review": ("supervisor", "Review"),
    "CommandVerifier": ("verify", "CommandVerifier"),
    "NullVerifier": ("verify", "NullVerifier"),
    "VerificationResult": ("verify", "VerificationResult"),
    "Verifier": ("verify", "Verifier"),
}

# Re-exported from a sibling package rather than a submodule of this one.
_LAZY_EXTERNAL: dict[str, tuple[str, str]] = {
    "SupportsComplete": ("chimera.providers", "SupportsComplete"),
}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name on first use, then cache it (PEP 562)."""
    external = _LAZY_EXTERNAL.get(name)
    if external is not None:
        module_path, attribute = external
    else:
        target = _LAZY.get(name)
        if target is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        submodule, attribute = target
        module_path = f"{__name__}.{submodule}"
    value = getattr(import_module(module_path), attribute)
    globals()[name] = value  # subsequent lookups skip __getattr__ entirely
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


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
    "RunCheckpointer",
    "build_repo_map",
    "RequirementChecklist",
    "SpecTestGenerator",
    "SpecTestVerifier",
    "workspace_digest",
    "StrongVerifier",
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
