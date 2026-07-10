"""Envelope verification (M16-A5): the gate between a worker's result and the orchestrator.

Evidence (MAST): ~21% of multi-agent failures are weak/absent verification, and a
chain at 0.95 per-hop quality still fails ~30% end-to-end when handoffs go
unchecked. This verifier sits between :func:`~chimera.orchestration.artifacts.build_envelope`
and the orchestrator's consumption, in three escalating gates:

1. **Schema** — free, deterministic (:func:`validate_envelope`).
2. **Acceptance criteria** — cheap/deterministic: contract clauses derived from
   the TaskSpec (``answer_matches`` etc.) evaluated against the summary.
3. **Spot check** — probabilistic (or forced when the worker self-reports gaps):
   an auditor model pulls the RAW artifact via ``evidence_refs`` and grades whether
   the summary is faithful to it. The artifact enters the VERIFIER's context
   only — never the orchestrator's. This is the escape hatch for "results too
   compressed to verify".

M18-2 hardening (arXiv 2607.00563 + 2607.06799): the auditor **re-derives** its judgement from the
raw output and never trusts the summary's self-report; the check is **decomposed** into named failure
classes (invented / dropped / contradiction), each graded separately (a single holistic verdict
under-discriminates); and the auditor can run on a **distinct provider** (``verifier_backend``) so a
model does not grade its own family's output — measured cross-provider auditing (0.82 AUROC) beats a
same-model judge (0.72-0.78), and fine-tuned verifiers overfit.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Literal

from chimera.core.contract import CompletionContract
from chimera.orchestration.artifacts import ArtifactStore
from chimera.orchestration.spec import ResultEnvelope, TaskSpec, validate_envelope
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("orchestration.envelope_verify")

VerifyStage = Literal["schema", "criteria", "spot", "accepted"]

_SPOT_SYSTEM = (
    "You are a strict, INDEPENDENT verification auditor. You receive a task, a worker's SUMMARY, and "
    "the worker's RAW OUTPUT. Do NOT trust the summary — re-derive every judgement from the RAW OUTPUT "
    "yourself. Grade three separate checks, each PASS or FAIL:\n"
    "INVENTED: the summary asserts a finding or result the raw output does not support -> FAIL.\n"
    "DROPPED: the summary omits a critical result the raw output shows -> FAIL.\n"
    "CONTRADICTION: the summary contradicts the raw output anywhere -> FAIL.\n"
    "Reply with exactly three lines — 'INVENTED: PASS|FAIL', 'DROPPED: PASS|FAIL', "
    "'CONTRADICTION: PASS|FAIL' — then one short justification sentence. The summary is faithful ONLY "
    "if all three are PASS."
)

# Named decomposed checks; any one FAILing (or the legacy holistic 'UNFAITHFUL') fails the spot check.
_CRITERIA = ("INVENTED", "DROPPED", "CONTRADICT")


def _grade_faithfulness(text: str) -> bool:
    """True if the auditor's reply indicates faithfulness. Handles the decomposed and legacy formats.

    Any named criterion marked FAIL, or the legacy holistic 'UNFAITHFUL', means unfaithful. A garbled
    or empty reply is treated as faithful — the spot check is a probabilistic sampler layered on the
    deterministic gates, so an unparseable audit must not falsely reject a result.
    """
    up = text.upper()
    if any(re.search(rf"{key}\w*\s*[:=-]?\s*FAIL", up) for key in _CRITERIA):
        return False
    return "UNFAITHFUL" not in up

#: Cap on how much raw artifact the spot-checker reads (its context, not the orchestrator's).
_SPOT_ARTIFACT_CHARS = 24_000


@dataclass
class VerifyOutcome:
    """The verdict for one envelope, with the stage that decided it."""

    passed: bool
    stage: VerifyStage
    detail: str = ""
    escalate: bool = False
    """True when the spot check disagreed with the summary — the orchestrator
    should treat the envelope as suspect (re-ask or read evidence itself)."""


class EnvelopeVerifier:
    """Three-gate verifier: schema (free) -> criteria (deterministic) -> spot (cheap model)."""

    def __init__(
        self,
        *,
        store: ArtifactStore,
        backend: SupportsComplete | None = None,
        model: str | None = None,
        verifier_backend: SupportsComplete | None = None,
        verifier_model: str | None = None,
        spot_rate: float = 0.2,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.backend = backend
        self.model = model
        # Cross-provider auditing (M18-2): the spot checker prefers a DISTINCT provider/model so a
        # model never grades its own family's output. Falls back to the worker's backend when none is
        # given (still a re-derivation from the raw artifact, just not provider-independent).
        self._spot_backend = verifier_backend or backend
        self._spot_model = verifier_model if verifier_backend is not None else (verifier_model or model)
        self.spot_rate = max(0.0, min(1.0, spot_rate))
        self.rng = rng or random.Random()

    def verify(self, spec: TaskSpec, envelope: ResultEnvelope) -> VerifyOutcome:
        """Run the gates in order; the first failure decides. All-pass -> accepted."""
        # Gate 1 — schema (free).
        problems = validate_envelope(spec, envelope)
        if problems:
            return VerifyOutcome(passed=False, stage="schema", detail="; ".join(problems))

        # Gate 2 — acceptance criteria (deterministic, no model).
        contract = _contract_from_spec(spec)
        if contract:
            result = contract.evaluate(envelope.summary)
            if not result.satisfied:
                return VerifyOutcome(
                    passed=False, stage="criteria", detail="; ".join(result.failures)
                )

        # Gate 3 — spot check (probabilistic; forced when the worker admits gaps).
        should_spot = bool(envelope.gaps) or self.rng.random() < self.spot_rate
        if should_spot and envelope.evidence_refs and self._spot_backend is not None:
            outcome = self._spot_check(spec, envelope)
            if outcome is not None:
                return outcome

        return VerifyOutcome(passed=True, stage="accepted")

    def _spot_check(self, spec: TaskSpec, envelope: ResultEnvelope) -> VerifyOutcome | None:
        """Grade summary faithfulness against the raw artifact. None = check unavailable."""
        try:
            raw = self.store.get(envelope.evidence_refs[0])
        except (FileNotFoundError, OSError):
            return VerifyOutcome(
                passed=False,
                stage="spot",
                detail=f"evidence ref {envelope.evidence_refs[0]!r} could not be read",
                escalate=True,
            )
        prompt = (
            f"## Task\n{spec.objective}\n\n"
            f"## Worker summary\n{envelope.summary}\n\n"
            f"## Raw output (may be truncated)\n{raw[:_SPOT_ARTIFACT_CHARS]}"
        )
        try:
            result = self._spot_backend.complete(  # type: ignore[union-attr]
                [
                    {"role": "system", "content": _SPOT_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                model=self._spot_model,
                temperature=0.0,
            )
        except Exception as exc:  # spot check must never take the pipeline down
            _log.warning("spot check unavailable (%s) — passing through un-spotted", exc)
            return None
        content = (result.content or "").strip()
        if _grade_faithfulness(content):
            return VerifyOutcome(passed=True, stage="spot", detail=content)
        return VerifyOutcome(
            passed=False,
            stage="spot",
            detail=content or "spot checker judged the summary unfaithful",
            escalate=True,
        )


def _contract_from_spec(spec: TaskSpec) -> CompletionContract | None:
    """Derive deterministic acceptance clauses from the spec.

    Convention: ``output_format`` lines starting with ``regex:`` become
    ``answer_matches`` clauses against the summary. (File-based clauses don't
    apply here — the envelope is text; workspace checks belong to the worker's
    own contract.)
    """
    specs = [
        f"answer_matches:{line.strip()[len('regex:'):].strip()}"
        for line in spec.output_format.splitlines()
        if line.strip().lower().startswith("regex:")
    ]
    if not specs:
        return None
    return CompletionContract.from_specs(specs)
