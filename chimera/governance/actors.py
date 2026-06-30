"""Four-actor change governance (Spec Growth Engine).

A change flows through four explicit governance roles before it lands:

- **AUTHOR**     — who/what proposed it (recorded).
- **REVIEWER**   — an advisory evaluation (approve + notes); does not decide.
- **GATEKEEPER** — the authoritative hard gate (a verifier / drift check / validator):
  pass or reject. This is what decides acceptance.
- **AUDITOR**    — records the decision to the audit log.

Separating *advice* (reviewer) from *authority* (gatekeeper) keeps a strict reviewer
from blocking verified-correct work, and an auditor trail makes every decision
inspectable — the governance discipline applied to changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from chimera.governance.audit import AuditLog
from chimera.telemetry import get_logger

_log = get_logger("governance.actors")

Reviewer = Callable[["ChangeProposal"], tuple[bool, str]]
Gatekeeper = Callable[["ChangeProposal"], tuple[bool, str]]


@dataclass
class ChangeProposal:
    id: str
    summary: str
    author: str = "agent"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActorResult:
    actor: str
    passed: bool
    detail: str = ""


@dataclass
class GovernanceDecision:
    proposal_id: str
    accepted: bool
    actors: list[ActorResult]


class FourActorGovernance:
    """Runs a proposal through author -> reviewer -> gatekeeper -> auditor."""

    def __init__(
        self,
        gatekeeper: Gatekeeper,
        *,
        reviewer: Reviewer | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self.gatekeeper = gatekeeper
        self.reviewer = reviewer
        self.audit = audit

    def decide(self, proposal: ChangeProposal) -> GovernanceDecision:
        actors: list[ActorResult] = [
            ActorResult("author", True, f"proposed by {proposal.author}")
        ]

        if self.reviewer is not None:
            approved, notes = self.reviewer(proposal)  # advisory only
            actors.append(ActorResult("reviewer", approved, notes))

        passed, detail = self.gatekeeper(proposal)  # authoritative
        actors.append(ActorResult("gatekeeper", passed, detail))
        accepted = passed

        if self.audit is not None:
            self.audit.record(
                "change_governance",
                {"proposal": proposal.id, "accepted": accepted, "summary": proposal.summary[:200]},
            )
            actors.append(ActorResult("auditor", True, "recorded"))
        else:
            actors.append(ActorResult("auditor", True, "no audit sink configured"))

        _log.debug("change %s -> accepted=%s", proposal.id, accepted)
        return GovernanceDecision(proposal.id, accepted, actors)
