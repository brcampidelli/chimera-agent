"""Small, deterministic gates for evidence-backed evolution artifacts."""

from __future__ import annotations

import re

# StepLog IDs are UUID4 hex strings. A human label or generated placeholder is not provenance.
_TRACE_ID = re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE)
# Evaluator prose is not an observation of the work. Do not distill it as a trigger, lesson,
# failed/passed example, or evidence reference.
_EVALUATOR_TEXT = re.compile(
    r"\b(?:grader|evaluator|judge)\s+(?:verdict|score|feedback|result)\b"
    r"|\b(?:the )?(?:grader|evaluator|judge) says\b",
    re.IGNORECASE,
)


def has_evidence(evidence_id: str | None) -> bool:
    """Evidence must be an ID propagated from StepLog's trace/receipt."""
    return isinstance(evidence_id, str) and bool(_TRACE_ID.fullmatch(evidence_id))


def contains_evaluator_text(*values: str) -> bool:
    """Identify explicit evaluator-attributed prose without rejecting ordinary task language."""
    return any(_EVALUATOR_TEXT.search(value or "") for value in values)


def evidence_rejection(
    *, evidence_id: str | None, text: tuple[str, ...], evaluator_text: tuple[str, ...] = ()
) -> str | None:
    """Return a reason when an artifact lacks provenance or was sourced from evaluator prose.

    Evaluator attribution is checked only on the separately identified evaluator output, not on
    an agent's solution or answer.
    """
    if len(text) < 2 or not text[0].strip() or not any(value.strip() for value in text[1:]):
        return "missing trigger or lesson"
    if not has_evidence(evidence_id):
        return "missing evidence id"
    if contains_evaluator_text(*evaluator_text):
        return "evaluator text is not learning evidence"
    return None
