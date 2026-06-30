"""Governance/safety kernel: allow/warn/block/review + static validator + audit.

A self-improving trust layer (lexical rules + optional semantic judge). Never
hard-blocks a benign action. Self-modification is only accepted through a
statically-validated edit surface.
"""

from chimera.governance.actors import (
    ActorResult,
    ChangeProposal,
    FourActorGovernance,
    GovernanceDecision,
)
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
from chimera.governance.policy import Decision, Rule, RuleSet, Verdict
from chimera.governance.precedent import PrecedentStore
from chimera.governance.validator import (
    ScheduleValidator,
    SkillValidator,
    ValidationResult,
)

__all__ = [
    "Decision",
    "Verdict",
    "Rule",
    "RuleSet",
    "TrustKernel",
    "AuditLog",
    "GovernedTool",
    "govern_registry",
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
