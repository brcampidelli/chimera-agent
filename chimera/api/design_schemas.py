"""Request and response models of ``/api/design`` (Chimera Design, PR D2).

Kept beside :mod:`chimera.api.schemas` rather than inside it: they belong to one feature, and that
module is already past three thousand lines.

**The document and the operations travel as JSON objects here, not as typed models.** The desktop
already has the TypeScript model of the document and its operations (`apps/desktop/src/lib/design/`),
written to the same camelCase wire format the domain validates. Generating a second TypeScript copy
of the same document from these Pydantic models would give the app two definitions of one thing,
free to drift. So the OpenAPI document says "an object" and the server validates it strictly with the
domain's own models (:mod:`chimera.design.model`, :func:`chimera.design.ops.parse_operations`), whose
refusals are short and never echo the offending value.

Every class is prefixed ``Design``: OpenAPI names a component after the class, and two classes with
one name collapse into one component (``tests/test_no_two_api_models_share_a_name.py``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from chimera.design.model import MAX_OPS_PER_CALL, MAX_TITLE
from chimera.design.propose import MAX_BRIEF, MAX_SELECTED

#: The id pattern of everything the server names (``chimera.design.model.ID_PATTERN``).
_ID = r"^[A-Za-z0-9_-]{1,100}$"


class DesignSummaryOut(BaseModel):
    id: str
    title: str
    updated_at: str
    revision: int


class DesignVersionOut(BaseModel):
    revision: int
    title: str
    updated_at: str


class DesignCreateIn(BaseModel):
    """A new, empty design. The id is the server's; ``tokens`` is a design system in the wire format
    (the default one when absent)."""

    title: str = Field(default="", max_length=MAX_TITLE)
    design_system_id: str | None = Field(default=None, pattern=_ID)
    tokens: dict[str, Any] | None = None


class DesignOpsIn(BaseModel):
    """Operations to apply as one batch, against the revision the client last saw.

    ``origin`` says where the operations came from. ``proposal`` (the client applying what
    ``/propose`` or ``/refine`` returned) is held to the limits a model's draft has: no locking or
    unlocking, and the proposal's smaller batch cap.
    """

    expected_revision: int = Field(ge=0)
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=MAX_OPS_PER_CALL)
    origin: Literal["manual", "proposal"] = "manual"


class DesignOpsOut(BaseModel):
    document: dict[str, Any]
    inverse: list[dict[str, Any]]
    """Operations that undo this batch exactly, to apply against the returned revision."""


class DesignRestoreIn(BaseModel):
    """Make an old revision the newest. ``confirm`` is the person's yes: the current content stays in
    the history, but the canvas changes under them."""

    revision: int = Field(ge=1)
    confirm: bool = False
    expected_revision: int | None = Field(default=None, ge=0)
    """The revision the client is looking at; when absent, whatever is current is replaced."""


class DesignConfirmIn(BaseModel):
    """Deleting removes the design and its whole history; it must be asked for in so many words."""

    confirm: bool = False


class DesignDeletedOut(BaseModel):
    deleted: bool


class DesignProposeIn(BaseModel):
    brief: str = Field(min_length=1, max_length=MAX_BRIEF)
    constraints: dict[str, Any] = Field(default_factory=dict)
    """Format, audience, sizes and the like. Sent to the model fenced, as data."""


class DesignRefineIn(BaseModel):
    request: str = Field(min_length=1, max_length=MAX_BRIEF)
    selected_ids: list[str] = Field(default_factory=list, max_length=MAX_SELECTED)


class DesignAuditIn(BaseModel):
    brief: str = Field(default="", max_length=MAX_BRIEF)


class DesignProposalOut(BaseModel):
    """What the model proposed, validated and dry-run — NOT applied.

    The client shows it, and applies ``operations`` through ``/ops`` with ``expected_revision`` set
    to ``base_revision`` and ``origin: "proposal"``. ``ok`` is false when the reply could not be
    used; ``error`` says why, and ``operations`` may still hold what the model tried.
    """

    ok: bool
    base_revision: int
    summary: str = ""
    assumptions: list[str] = Field(default_factory=list)
    operations: list[dict[str, Any]] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    preview: dict[str, Any] | None = None
    """The document as it would be after the operations, for the canvas to preview."""
    error: str | None = None
    attempts: int = 0
    usd: float | None = None
    """What the call cost; null when a model's price is unknown."""


class DesignFindingOut(BaseModel):
    """A deterministic check of the document against its tokens (no model)."""

    kind: str
    node_id: str | None = None
    artboard_id: str | None = None
    message: str


class DesignCheckOut(BaseModel):
    revision: int
    findings: list[DesignFindingOut]


class DesignAuditIssueOut(BaseModel):
    severity: Literal["high", "medium", "low"]
    node_id: str | None = None
    evidence: str
    fix: str = ""


class DesignAuditOut(BaseModel):
    ok: bool
    revision: int
    issues: list[DesignAuditIssueOut] = Field(default_factory=list)
    passed_checks: list[str] = Field(default_factory=list)
    needs_user_decision: list[str] = Field(default_factory=list)
    findings: list[DesignFindingOut] = Field(default_factory=list)
    unverifiable: int = 0
    """Issues the model pinned on an element the document does not have — dropped, counted."""
    error: str | None = None
    attempts: int = 0
    usd: float | None = None


class DesignExportIn(BaseModel):
    """``format`` is a string rather than an enum so that asking for a format this version cannot
    write gets a sentence saying so, not a schema error."""

    format: str = Field(default="html", max_length=20)
    confirm: bool = False
