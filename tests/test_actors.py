"""Tests for the four-actor change governance (Spec Growth Engine)."""

from __future__ import annotations

from pathlib import Path

from chimera.governance import AuditLog, ChangeProposal, FourActorGovernance


def _proposal() -> ChangeProposal:
    return ChangeProposal(id="c1", summary="add feature X", author="dev")


def test_accepted_when_gatekeeper_passes() -> None:
    gov = FourActorGovernance(gatekeeper=lambda p: (True, "tests green"))
    decision = gov.decide(_proposal())
    assert decision.accepted is True
    assert [a.actor for a in decision.actors] == ["author", "gatekeeper", "auditor"]


def test_rejected_when_gatekeeper_fails() -> None:
    gov = FourActorGovernance(gatekeeper=lambda p: (False, "tests failed"))
    assert gov.decide(_proposal()).accepted is False


def test_reviewer_is_advisory_not_authoritative() -> None:
    # reviewer rejects but the gatekeeper passes -> accepted (reviewer is advice only)
    gov = FourActorGovernance(
        gatekeeper=lambda p: (True, "verified"),
        reviewer=lambda p: (False, "I have doubts"),
    )
    decision = gov.decide(_proposal())
    assert decision.accepted is True
    review = next(a for a in decision.actors if a.actor == "reviewer")
    assert review.passed is False and "doubts" in review.detail


def test_auditor_records_the_decision(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    gov = FourActorGovernance(gatekeeper=lambda p: (True, "ok"), audit=audit)
    decision = gov.decide(_proposal())
    assert len(audit) == 1
    assert any(a.actor == "auditor" and a.passed for a in decision.actors)
