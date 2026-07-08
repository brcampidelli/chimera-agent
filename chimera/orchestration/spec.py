"""Delegation contract: the schema-validated handoff between orchestrator and workers.

Evidence (MAST, 1,600 annotated multi-agent traces): 41.8% of failures are vague
task specifications and 36.9% are context lost at handoffs. The fix is a contract:
a :class:`TaskSpec` travels DOWN with explicit objective/format/boundaries/budget,
and a :class:`ResultEnvelope` travels UP with a bounded summary plus artifact
references — never a transcript. Bulky output lives in the artifact store (M16-A2);
the orchestrator only ever sees the envelope.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

EnvelopeStatus = Literal["ok", "partial", "failed"]

#: Hard cap on the summary a worker may return to its orchestrator, in characters.
#: ~2k tokens at 4 chars/token — the Anthropic-reported sweet spot for subagent
#: returns (workers may burn tens of thousands of tokens internally; the parent
#: pays only for this).
SUMMARY_MAX_CHARS = 8_000


class EffortBudget(BaseModel):
    """How much a single delegation is allowed to spend.

    Enforced by the harness (``BudgetedBackend``, M16-A4), not by prompt begging.
    """

    max_tokens: int = 8_000
    max_steps: int = 6


class TaskSpec(BaseModel):
    """The delegation contract handed to a worker.

    Every field exists to kill a documented failure mode: ``objective`` +
    ``output_format`` + ``boundaries`` against vague specs; ``allowed_tools``
    against scope creep; ``effort`` against runaway spend; ``context`` carries
    only the volatile task-specific material (it is rendered AFTER the worker's
    static system prefix so tier-wide prompt caching stays warm).
    """

    task_id: str
    objective: str
    output_format: str = ""
    """Prose description of the expected result shape ("a markdown table of ...")."""
    output_schema: dict[str, Any] | None = None
    """Optional JSON schema the result must parse against (stricter than prose)."""
    allowed_tools: list[str] = Field(default_factory=list)
    boundaries: str = ""
    """Explicit non-goals / scope limits ("do not modify files", "sources X only")."""
    effort: EffortBudget = Field(default_factory=EffortBudget)
    context: str = ""
    """Volatile task-specific context — goes after the cached static prefix."""

    def render(self) -> str:
        """Render the spec as the worker-facing task message."""
        parts = [f"## Objective\n{self.objective.strip()}"]
        if self.output_format:
            parts.append(f"## Expected output\n{self.output_format.strip()}")
        if self.boundaries:
            parts.append(f"## Boundaries (do not exceed)\n{self.boundaries.strip()}")
        if self.allowed_tools:
            parts.append("## Allowed tools\n" + ", ".join(self.allowed_tools))
        if self.context:
            parts.append(f"## Context\n{self.context.strip()}")
        parts.append(
            "## Result contract\nReturn a concise result (do not echo the context). "
            "If your output is long, lead with a summary of the findings. "
            "List anything you could not verify under a final 'Gaps' heading."
        )
        return "\n\n".join(parts)


class ResultEnvelope(BaseModel):
    """What the orchestrator receives back — never the worker's transcript.

    ``summary`` is bounded (see :data:`SUMMARY_MAX_CHARS`); anything bigger is
    spilled to the artifact store and referenced via ``evidence_refs`` so a
    verifier can audit the raw output without it ever entering the
    orchestrator's context.
    """

    task_id: str
    status: EnvelopeStatus = "ok"
    summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    """Artifact-store paths holding the raw output / supporting material."""
    gaps: list[str] = Field(default_factory=list)
    """Self-reported gaps: what the worker could not do or verify."""
    receipt: dict[str, Any] | None = None
    """Delegation receipt (tokens/cost/counterfactual) — attached in M16-A3."""


def schema_problem(spec: TaskSpec, envelope: ResultEnvelope) -> str | None:
    """If the spec pins an ``output_schema``, check the summary parses against it.

    Best-effort and free: JSON-decodes the summary (tolerating a fenced block)
    and verifies top-level ``required`` keys. ``None`` means no problem.
    """
    if spec.output_schema is None or envelope.status != "ok":
        return None
    text = envelope.summary.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return "output_schema is set but summary is not valid JSON"
    required = spec.output_schema.get("required")
    if isinstance(required, list) and isinstance(data, dict):
        missing = [key for key in required if key not in data]
        if missing:
            return f"summary JSON is missing required keys: {', '.join(sorted(missing))}"
    return None


def validate_envelope(spec: TaskSpec, envelope: ResultEnvelope) -> list[str]:
    """Free (no-model) schema gate: contract violations as a list of problems.

    Empty list = envelope is structurally acceptable. This is verification
    stage 1 of 3 (schema -> acceptance criteria -> spot check, M16-A5).
    """
    problems: list[str] = []
    if envelope.task_id != spec.task_id:
        problems.append(
            f"task_id mismatch: spec={spec.task_id!r} envelope={envelope.task_id!r}"
        )
    if envelope.status == "ok" and not envelope.summary.strip():
        problems.append("status is 'ok' but summary is empty")
    if len(envelope.summary) > SUMMARY_MAX_CHARS:
        problems.append(
            f"summary exceeds cap ({len(envelope.summary)} > {SUMMARY_MAX_CHARS} chars); "
            "bulk output must be spilled to an artifact and referenced"
        )
    if envelope.status == "failed" and not (envelope.gaps or envelope.summary.strip()):
        problems.append("status is 'failed' with no explanation (empty summary and gaps)")
    schema_issue = schema_problem(spec, envelope)
    if schema_issue:
        problems.append(schema_issue)
    return problems
