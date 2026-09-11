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
from dataclasses import dataclass, replace
from typing import Literal

from chimera.core.contract import CompletionContract
from chimera.orchestration.artifacts import ArtifactStore
from chimera.orchestration.spec import ResultEnvelope, TaskSpec, validate_envelope
from chimera.providers.gateway import SupportsComplete
from chimera.telemetry import get_logger

_log = get_logger("orchestration.envelope_verify")

VerifyStage = Literal["schema", "criteria", "spot", "accepted"]

#: The three-check prompt the spot check ran until 2026-09-11, kept for `bench/blind_audit`'s
#: `shipped` arm so the number it measured stays reproducible. The spot check runs only where
#: `build_envelope` spilled the output and the summary is `_distill`'s head + tail SLICE of it, so
#: two of these three checks cannot fail for a true reason there: a verbatim slice asserts nothing
#: the raw output does not, and contradicts nothing in it. Measured (RESULTS.md there): when this
#: prompt failed an item it marked INVENTED and CONTRADICTION as often as DROPPED.
_SPOT_SYSTEM_THREE_CHECKS = (
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
#: What the spot check asks on the only path it runs on. The summary it is handed is a mechanical
#: slice — the first 70% and the last 15% of the raw output, with a marker between — so the one
#: thing that can be wrong with it is what the cut removed, and that is the one thing graded.
_SPOT_SYSTEM_DROPPED_ONLY = (
    "You are a strict, INDEPENDENT verification auditor. You receive a task, a worker's RAW OUTPUT, "
    "and a SUMMARY that is a mechanical slice of that output: its beginning and its end, with the "
    "middle cut out at the marker. Nothing in the summary was written by anyone, so it cannot invent "
    "or contradict; what it can do is OMIT. Read the RAW OUTPUT yourself and grade one check:\n"
    "DROPPED: the summary omits a critical result the raw output shows — a failure, a security or "
    "data exposure, an escalation, a blocker, anything a reader of this task must not miss -> FAIL.\n"
    "Reply with exactly one line — 'DROPPED: PASS|FAIL' — then one short sentence naming the "
    "omitted result, or saying that none is critical."
)
_SPOT_SYSTEM = _SPOT_SYSTEM_DROPPED_ONLY

# Named decomposed checks; any one FAILing (or the legacy holistic 'UNFAITHFUL') fails the spot check.
_CRITERIA = ("INVENTED", "DROPPED", "CONTRADICT")

# The blind audit (arXiv 2609.07680, `bench/blind_audit`): stage 1 reads the RAW OUTPUT and never the
# summary, so the worker's leading conclusion cannot shape what it lists; stage 2 reads the list and
# the summary and never the raw output, so it can only compare. Measured 2026-09-11 on 23 worker
# outputs with one critical finding planted where `_distill` cuts: the shipped one-call auditor above
# (summary + raw + "do not trust") said DROPPED on **4 of 23**; this two-call form on **19 of 23** —
# and on 11 of 23 summaries that dropped nothing, which is the paper's stated cost and why the
# result is a RECOVERY (the absent findings are appended to the summary) and never a rejection.
EXTRACT_SYSTEM = (
    "You are a strict verification auditor. You receive a task and a worker's RAW OUTPUT for it. "
    "List every result the raw output establishes that the task asks for — one finding per line, "
    "numbered. Mark with [CRITICAL] any finding a reader of this task must not miss: a failure, a "
    "security or data exposure, an escalation, a blocker, a contradiction with what the task expects. "
    "Do not summarise, do not judge quality, do not add findings the raw output does not contain. "
    "Reply with the numbered list only."
)
COMPARE_SYSTEM = (
    "You compare a numbered list of findings against a SUMMARY of the same work. For each finding, "
    "reply on its own line with its number and PRESENT if the summary conveys that finding (same "
    "substance, any wording) or ABSENT if it does not. Then reply with exactly one final line: "
    "'DROPPED: FAIL' if any finding marked [CRITICAL] is ABSENT, otherwise 'DROPPED: PASS'."
)
_FINDING = re.compile(r"^\s*(\d+)[.)]?\s*(.+?)\s*$", re.MULTILINE)
_VERDICT_LINE = re.compile(r"^\s*(\d+)[.)]?\s*[:\-]?\s*(PRESENT|ABSENT)\b", re.IGNORECASE | re.MULTILINE)
#: How many recovered findings a summary may grow by, and how long each may be. A bound, because
#: the extractor's list is model-written and an unbounded append would make the audit the loudest
#: voice in the synthesis.
_MAX_RECOVERED = 6
_MAX_RECOVERED_CHARS = 300


def absent_critical(findings: str, verdicts: str) -> list[str]:
    """The [CRITICAL] findings stage 2 marked ABSENT, in stage 1's order, bounded and clean."""
    listed = {int(num): text for num, text in _FINDING.findall(findings)}
    absent = {int(num) for num, status in _VERDICT_LINE.findall(verdicts) if status.upper() == "ABSENT"}
    out: list[str] = []
    for num in sorted(absent):
        text = listed.get(num, "")
        if "[critical]" not in text.lower():
            continue
        clean = " ".join(text.replace("[CRITICAL]", "").replace("[critical]", "").split())
        if clean:
            out.append(clean[:_MAX_RECOVERED_CHARS])
        if len(out) >= _MAX_RECOVERED:
            break
    return out


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
    checks_run: tuple[str, ...] = ()
    """Which gates actually EXECUTED, in order — not which ones exist.

    `stage="accepted"` only ever meant "no gate rejected", and for ordinary output that is one gate:
    criteria needs `regex:` lines in an `output_format` written as prose by a model, and the spot
    check needs `evidence_refs`, which `build_envelope` fills in only when the output overruns the
    8000-character cap. Both are skipped by construction, so `accepted` was reported for a verdict
    that had checked shape and nothing else — and the screen rendered it as "verificado".

    Naming what ran is the difference between a claim and a receipt. The UI reads this."""
    escalate: bool = False
    """True when the spot check disagreed with the summary — the orchestrator
    should treat the envelope as suspect (re-ask or read evidence itself)."""
    recovered: tuple[str, ...] = ()
    """Critical findings the blind audit found in the raw output and not in the summary. Never a
    reason to fail: the orchestrator appends them to the summary so the synthesis can see what the
    distillation cut. Empty when the audit did not run or found nothing absent."""


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
        recover_dropped: bool = True,
        spot_system: str = _SPOT_SYSTEM,
    ) -> None:
        self.store = store
        self.backend = backend
        self.model = model
        #: The spot check's system prompt. A parameter so `bench/blind_audit` can run the prompt that
        #: was measured beside the one that ships; production never passes it.
        self.spot_system = spot_system
        #: Whether a spot check is followed by the blind audit that RECOVERS dropped findings. On by
        #: default: it runs only where the spot check runs (evidence on disk, sampled or forced), it
        #: costs two more calls there, and its output is an append, never a verdict.
        self.recover_dropped = recover_dropped
        # Cross-provider auditing (M18-2): the spot checker prefers a DISTINCT provider/model so a
        # model never grades its own family's output. Falls back to the worker's backend when none is
        # given (still a re-derivation from the raw artifact, just not provider-independent).
        self._spot_backend = verifier_backend or backend
        self._spot_model = verifier_model if verifier_backend is not None else (verifier_model or model)
        self.spot_rate = max(0.0, min(1.0, spot_rate))
        self.rng = rng or random.Random()

    def verify(
        self, spec: TaskSpec, envelope: ResultEnvelope, *, force_spot: bool = False
    ) -> VerifyOutcome:
        """Run the gates in order; the first failure decides. All-pass -> accepted.

        ``force_spot`` runs the spot check unconditionally — used when re-verifying a re-ask that was
        triggered by a spot failure, so the expensive audit that caught the unfaithfulness isn't then
        skipped ~80% of the time on the retry (which could re-accept a still-unfaithful summary).
        """
        ran: list[str] = []

        # Gate 1 — schema (free). Always runs, which is exactly why naming it matters: on its own
        # it asserts "non-empty text, right task_id, under the cap" and nothing about the content.
        ran.append("schema")
        problems = validate_envelope(spec, envelope)
        if problems:
            return VerifyOutcome(
                passed=False, stage="schema", detail="; ".join(problems), checks_run=tuple(ran)
            )

        # Gate 2 — acceptance criteria (deterministic, no model).
        contract = _contract_from_spec(spec)
        if contract:
            ran.append("criteria")
            result = contract.evaluate(envelope.summary)
            if not result.satisfied:
                return VerifyOutcome(
                    passed=False, stage="criteria", detail="; ".join(result.failures),
                    checks_run=tuple(ran),
                )

        # Gate 3 — spot check (probabilistic; forced when the worker admits gaps or on a re-ask).
        should_spot = force_spot or bool(envelope.gaps) or self.rng.random() < self.spot_rate
        if should_spot and envelope.evidence_refs and self._spot_backend is not None:
            ran.append("spot")
            outcome = self._spot_check(spec, envelope)
            if outcome is not None:
                if outcome.passed and self.recover_dropped:
                    # The shipped auditor passed the summary. It passes summaries that dropped a
                    # critical finding 19 times in 23 (`bench/blind_audit`), so the blind audit runs
                    # behind it and hands back what the distillation cut. Recovery, not a verdict.
                    ran.append("recover")
                    outcome = replace(outcome, recovered=self._recover_dropped(spec, envelope))
                return replace(outcome, checks_run=tuple(ran))

        return VerifyOutcome(passed=True, stage="accepted", checks_run=tuple(ran))

    def _recover_dropped(self, spec: TaskSpec, envelope: ResultEnvelope) -> tuple[str, ...]:
        """The blind audit: extract from the raw output alone, compare against the summary alone.

        Returns the critical findings the summary lacks; empty on any failure, because a recovery
        that cannot run is a summary left as it was, which is what shipped before it existed.
        """
        try:
            raw = self.store.get(envelope.evidence_refs[0])
            stage1 = self._spot_backend.complete(  # type: ignore[union-attr]
                [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": (
                        f"## Task\n{spec.objective}\n\n## Raw output (may be truncated)\n"
                        f"{raw[:_SPOT_ARTIFACT_CHARS]}"
                    )},
                ],
                model=self._spot_model,
                temperature=0.0,
            )
            findings = (stage1.content or "").strip()
            if "[critical]" not in findings.lower():
                return ()
            stage2 = self._spot_backend.complete(  # type: ignore[union-attr]
                [
                    {"role": "system", "content": COMPARE_SYSTEM},
                    {"role": "user", "content": f"## Findings\n{findings}\n\n## Summary\n{envelope.summary}"},
                ],
                model=self._spot_model,
                temperature=0.0,
            )
            return tuple(absent_critical(findings, (stage2.content or "").strip()))
        except Exception as exc:  # the recovery must never take the pipeline down
            _log.warning("blind audit unavailable (%s) — summary left as it was", exc)
            return ()

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
                    {"role": "system", "content": self.spot_system},
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
