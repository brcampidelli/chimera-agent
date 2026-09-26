"""What a review returns: the strict JSON form tools read, and the order people read it in.

The JSON form is the contract. ``extra="forbid"`` on every model means a field cannot appear in the
output without appearing here first, and ``SCHEMA`` names the version a consumer checks.

Three states are kept apart on purpose, because they read alike and mean opposite things:

- ``no_findings``: the reviewer read every file and nothing it reported survived the checks;
- ``incomplete``: some part of the change was never reviewed (a call failed, a reply could not be
  read, a file was too large), so silence proves nothing;
- ``empty``: there was no change to review.

A review that could not be read and a review that found nothing produce the same empty list of
findings. Reporting both as "no findings" is how a provider outage passes for a clean change.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA = "chimera.review/1"

Priority = Literal["P0", "P1", "P2", "P3"]
PRIORITIES: tuple[Priority, ...] = ("P0", "P1", "P2", "P3")
Status = Literal["findings", "no_findings", "incomplete", "empty"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Verdict(_Strict):
    """What the checks said about one finding.

    ``state`` is what the pipeline acts on; ``label`` is the verifier's own word for it. A verifier
    with three states (confirmed / plausible / refuted) maps its labels onto the two actions and
    keeps its label here, so swapping it in changes no consumer of this schema.
    """

    state: Literal["kept", "dropped", "unverified"]
    label: str
    reason: str = ""
    stage: Literal["anchor", "verifier", "none"] = "verifier"


class Finding(_Strict):
    id: str = ""
    priority: Priority
    file: str
    line: int
    title: str
    evidence: str = ""
    consequence: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verdict: Verdict | None = None


class Usage(_Strict):
    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float | None = 0.0


class Reviewer(_Strict):
    model: str
    family: str
    author_model: str
    author_family: str
    source: str
    same_family: bool
    verifier: str


class NotReviewed(_Strict):
    file: str
    reason: str


class ReviewReport(_Strict):
    schema_: str = Field(default=SCHEMA, alias="schema")
    experimental: bool = True
    status: Status
    base: str = ""
    base_label: str = ""
    target: str = ""
    files_changed: int = 0
    findings: list[Finding] = Field(default_factory=list)
    dropped: list[Finding] = Field(default_factory=list)
    residual_risks: list[str] = Field(default_factory=list)
    untested_paths: list[str] = Field(default_factory=list)
    not_reviewed: list[NotReviewed] = Field(default_factory=list)
    untracked_skipped: int = 0
    notes: list[str] = Field(default_factory=list)
    reviewer: Reviewer
    usage: Usage = Field(default_factory=Usage)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def to_json(self) -> str:
        return self.model_dump_json(by_alias=True, indent=2)


def order(findings: list[Finding]) -> list[Finding]:
    """P0 first; within a priority the more confident first; then by place in the change."""
    return sorted(
        findings,
        key=lambda f: (PRIORITIES.index(f.priority), -(f.confidence or 0.0), f.file, f.line),
    )


def number(findings: list[Finding], prefix: str) -> list[Finding]:
    """Give each finding a stable id in its final order (``F1``, ``F2``… or ``D1``… for dropped)."""
    return [f.model_copy(update={"id": f"{prefix}{i}"}) for i, f in enumerate(findings, 1)]
