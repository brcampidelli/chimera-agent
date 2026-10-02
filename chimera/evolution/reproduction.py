"""Failure-reproduction gate for anti-pattern cards (Phantom Guardrails, arXiv 2607.13083).

Self-improving harnesses can hallucinate the failures they later "fix": a proposer shown a
failure narrative invents a violation that never happened and distills a card for it. Measured
here at home before this gate existed: a Manager rejected an attempt whose receipt's own
``diff_summary`` proved the work was done, the attempt was reverted, and the hallucination
outlived the run as a permanent anti-pattern card (the comment at the review site in
:mod:`chimera.core.autonomous` records the verbatim).

The gate asks the question the proposer never does: **did the cited failure actually happen?**
It reads the run's own evidence — the attempts the run just finished, each carrying what decided
it (``evidence``) and whether an executable verifier failed it (``verified``) — and classifies
the narrative against three rules:

1. **A verifier-failed attempt is a reproduced failure.** A command that exited non-zero is
   ground truth; the failure needs no further proof.
2. **A failure narrated with no verifier and no diff is unproven.** Nothing executable spoke,
   nothing changed on disk — the narrative is the only evidence, which is exactly the shape of
   the fabricated failure the paper plants.
3. **A Manager rejection of verified-correct work is the paper's case, named.** ``evidence ==
   "verifier"`` with ``verified=True`` and the attempt still failed means a reviewer overrode
   executable ground truth — the hallucination is on the *reviewer's* side, and the card would
   memorialize it.

The verdict is three-valued on purpose, following :mod:`chimera.evolution.holdout`:
``reproduced`` / ``unproven`` / ``contradicted``. An unmeasured verdict never reads as a pass —
the caller decides what an ``unproven`` narrative is worth, but it may not silently become a
card. Every verdict is auditable, because a gate whose rejection rate is zero supports nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("evolution.reproduction")


@dataclass(frozen=True)
class ReproductionVerdict:
    """What the run's own evidence says about the failure the card cites.

    ``reproduced`` — at least one attempt was failed by an executable verifier, so the failure
    is grounded. ``contradicted`` — the evidence shows the cited failure never happened (a
    reviewer overrode a passing verifier). ``unproven`` — nothing executable spoke; the
    narrative is all there is. The three are deliberately not collapsed into a boolean: "we
    could not tell" is a different fact from "it did not happen", and a gate that merges them
    re-creates the silence this module exists to remove.
    """

    outcome: str  # "reproduced" | "unproven" | "contradicted"
    verifier_failed: int = 0
    attempts: int = 0
    reason: str = ""

    @property
    def reproduced(self) -> bool:
        return self.outcome == "reproduced"

    def summary(self) -> str:
        return self.reason or self.outcome


class FailureReproductionGate:
    """Classifies a failure narrative against the attempts that produced it."""

    def __init__(self, *, require_verifier: bool = False) -> None:
        #: ``False`` (default) keeps today's behaviour for verifier-less runs — the gate records
        #: ``unproven`` and the caller decides. ``True`` hard-fails them: for deployments where a
        #: card must never rest on narration alone. Opt-in because most legitimate runs of small
        #: tasks have no verifier at all, and a default-on gate here would stop learning entirely.
        self.require_verifier = require_verifier

    def evaluate(
        self,
        detail: str,
        attempts: Sequence[Any] | None = None,
    ) -> ReproductionVerdict:
        """Classify ``detail`` (the failure narrative) against ``attempts``.

        ``attempts`` are the run's own :class:`~chimera.core.autonomous.Attempt` objects — or
        anything with ``verified``/``evidence``/``success`` attributes; the gate reads three
        fields and nothing else, so tests can pass stubs. ``None`` or empty means the caller had
        no attempts to show, which is ``unproven`` with a reason, never a pass.
        """
        if not attempts:
            return ReproductionVerdict(
                "unproven",
                reason="no attempts were supplied; the narrative is the only evidence",
            )
        verifier_failed = [
            a for a in attempts
            if bool(getattr(a, "verified", False)) is False
            and str(getattr(a, "evidence", "")) == "verifier"
        ]
        if verifier_failed:
            n = len(verifier_failed)
            return ReproductionVerdict(
                "reproduced",
                verifier_failed=n,
                attempts=len(attempts),
                reason=f"{n} attempt(s) failed an executable verifier — ground truth",
            )
        # No verifier failed anything. Two ways that happens with very different meanings.
        overridden = [
            a for a in attempts
            if bool(getattr(a, "verified", False)) and str(getattr(a, "evidence", "")) == "verifier"
        ]
        if overridden:
            return ReproductionVerdict(
                "contradicted",
                attempts=len(attempts),
                reason=(
                    "a reviewer rejected work an executable verifier had passed — the cited "
                    "failure contradicts the run's own ground truth"
                ),
            )
        reason = (
            "no attempt was failed by an executable verifier; the failure rests on narration "
            "alone"
        )
        if self.require_verifier:
            return ReproductionVerdict("contradicted", attempts=len(attempts), reason=reason)
        return ReproductionVerdict("unproven", attempts=len(attempts), reason=reason)
