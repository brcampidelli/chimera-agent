"""Governance/safety kernel: allow/warn/block/review + static validator + audit.

A self-improving trust layer (lexical rules + optional semantic judge). Never
hard-blocks a benign action. Self-modification is only accepted through a
statically-validated edit surface.

**Why the re-exports are lazy.** See :mod:`chimera.eval` for the full rationale. In short: Python runs
this ``__init__`` before any submodule, so ``from chimera.governance.ledger import TaintLedger`` used
to drag in ``drift`` → ``chimera.core`` → ``autonomous`` → ``chimera.ecosystem`` as well. Names below
resolve on first access (PEP 562) and are cached, so ``from chimera.governance import TrustKernel``
still works; the ``TYPE_CHECKING`` block keeps mypy's view exact.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chimera.governance.actors import (
        ActorResult,
        ChangeProposal,
        FourActorGovernance,
        GovernanceDecision,
    )
    from chimera.governance.aggregate_monitor import AggregateMonitor, CollusionFinding
    from chimera.governance.allowlist import restrict_registry
    from chimera.governance.audit import AuditLog
    from chimera.governance.drift import (
        DriftReport,
        Requirement,
        Spec,
        check_drift,
        load_spec,
    )
    from chimera.governance.governed_tool import GovernedTool, govern_registry
    from chimera.governance.kernel import TrustKernel
    from chimera.governance.ledger import (
        CapabilityEvent,
        SequenceAssessment,
        SharedTaint,
        TaintLedger,
        assess_action,
    )
    from chimera.governance.ledger_tool import (
        DANGEROUS_WHEN_TAINTED,
        FENCE_CLOSE,
        FENCE_OPEN,
        LedgeredTool,
        fence,
        ledger_registry,
    )
    from chimera.governance.policy import Decision, Rule, RuleSet, Verdict
    from chimera.governance.precedent import PrecedentStore
    from chimera.governance.quarantine import (
        QuarantinedReader,
        QuarantineResult,
        fields_schema,
    )
    from chimera.governance.sanitize import (
        has_control_tokens,
        sanitize_untrusted,
        strip_leaked_control_tokens,
    )
    from chimera.governance.validator import (
        ScheduleValidator,
        SkillValidator,
        ValidationResult,
    )

# Exported name -> (submodule, attribute). No renames here, but the shape matches chimera.eval's.
_LAZY: dict[str, tuple[str, str]] = {
    "ActorResult": ("actors", "ActorResult"),
    "ChangeProposal": ("actors", "ChangeProposal"),
    "FourActorGovernance": ("actors", "FourActorGovernance"),
    "GovernanceDecision": ("actors", "GovernanceDecision"),
    "AggregateMonitor": ("aggregate_monitor", "AggregateMonitor"),
    "CollusionFinding": ("aggregate_monitor", "CollusionFinding"),
    "restrict_registry": ("allowlist", "restrict_registry"),
    "AuditLog": ("audit", "AuditLog"),
    "DriftReport": ("drift", "DriftReport"),
    "Requirement": ("drift", "Requirement"),
    "Spec": ("drift", "Spec"),
    "check_drift": ("drift", "check_drift"),
    "load_spec": ("drift", "load_spec"),
    "GovernedTool": ("governed_tool", "GovernedTool"),
    "govern_registry": ("governed_tool", "govern_registry"),
    "TrustKernel": ("kernel", "TrustKernel"),
    "CapabilityEvent": ("ledger", "CapabilityEvent"),
    "SequenceAssessment": ("ledger", "SequenceAssessment"),
    "SharedTaint": ("ledger", "SharedTaint"),
    "TaintLedger": ("ledger", "TaintLedger"),
    "assess_action": ("ledger", "assess_action"),
    "DANGEROUS_WHEN_TAINTED": ("ledger_tool", "DANGEROUS_WHEN_TAINTED"),
    "FENCE_CLOSE": ("ledger_tool", "FENCE_CLOSE"),
    "FENCE_OPEN": ("ledger_tool", "FENCE_OPEN"),
    "LedgeredTool": ("ledger_tool", "LedgeredTool"),
    "fence": ("ledger_tool", "fence"),
    "ledger_registry": ("ledger_tool", "ledger_registry"),
    "Decision": ("policy", "Decision"),
    "Rule": ("policy", "Rule"),
    "RuleSet": ("policy", "RuleSet"),
    "Verdict": ("policy", "Verdict"),
    "PrecedentStore": ("precedent", "PrecedentStore"),
    "QuarantinedReader": ("quarantine", "QuarantinedReader"),
    "QuarantineResult": ("quarantine", "QuarantineResult"),
    "fields_schema": ("quarantine", "fields_schema"),
    "has_control_tokens": ("sanitize", "has_control_tokens"),
    "sanitize_untrusted": ("sanitize", "sanitize_untrusted"),
    "strip_leaked_control_tokens": ("sanitize", "strip_leaked_control_tokens"),
    "ScheduleValidator": ("validator", "ScheduleValidator"),
    "SkillValidator": ("validator", "SkillValidator"),
    "ValidationResult": ("validator", "ValidationResult"),
}


def __getattr__(name: str) -> Any:
    """Resolve a re-exported name on first use, then cache it (PEP 562)."""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    submodule, attribute = target
    value = getattr(import_module(f"{__name__}.{submodule}"), attribute)
    globals()[name] = value  # subsequent lookups skip __getattr__ entirely
    return value


def __dir__() -> list[str]:
    return sorted(__all__)


__all__ = [
    "Decision",
    "Verdict",
    "Rule",
    "RuleSet",
    "TrustKernel",
    "AuditLog",
    "GovernedTool",
    "govern_registry",
    "restrict_registry",
    "TaintLedger",
    "SharedTaint",
    "CapabilityEvent",
    "SequenceAssessment",
    "assess_action",
    "AggregateMonitor",
    "CollusionFinding",
    "LedgeredTool",
    "ledger_registry",
    "fence",
    "FENCE_OPEN",
    "FENCE_CLOSE",
    "DANGEROUS_WHEN_TAINTED",
    "sanitize_untrusted",
    "strip_leaked_control_tokens",
    "has_control_tokens",
    "QuarantinedReader",
    "QuarantineResult",
    "fields_schema",
    "SkillValidator",
    "ScheduleValidator",
    "ValidationResult",
    "Spec",
    "Requirement",
    "DriftReport",
    "check_drift",
    "load_spec",
    "PrecedentStore",
    "FourActorGovernance",
    "ChangeProposal",
    "GovernanceDecision",
    "ActorResult",
]
