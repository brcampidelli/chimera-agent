"""``/api/design`` — Chimera Design documents over HTTP (PR D2).

The routes are a thin layer over the domain in :mod:`chimera.design`: the store keeps documents and
their history, :func:`~chimera.design.ops.apply_operations` is the only way a document changes, and
:mod:`chimera.design.propose` asks a model. What this module adds is the HTTP contract around them:

* **Optimistic concurrency.** ``POST /ops`` carries the revision the client last saw; a document that
  moved on answers 409 and is left alone, never silently overwritten.
* **A model never edits.** ``/propose`` and ``/refine`` return a validated, dry-run proposal and the
  preview; nothing is saved. The client applies the operations through ``/ops`` after the person has
  seen them, so every change an agent suggests passes the owner's screen first.
* **Confirmation where something is lost or leaves.** Restoring (the canvas changes under the person),
  deleting (the design and its history go) and exporting (the content leaves the app) are refused
  without ``confirm: true`` — the storage routes' pattern (:mod:`chimera.api.storage_api`).
* **Audit.** Every route that changes or exports a design, and every model call, records an event on
  ``audit.jsonl`` with ids and counts only. Document text never goes to the audit log: titles and
  canvas text are the owner's content, and the log is read on the Security screen.
* **Errors say what to fix, never what is inside.** Validation refusals are the domain's short
  messages, which name the field and never echo its value; a document that cannot be read or a disk
  that refuses a write answers with a generic sentence, the detail going to the log.

On the bridge, the reads, the token check, ``ops`` and the three model calls are ``operate``;
restore, delete and export are not reachable at all (see the note in
:mod:`chimera.api.bridge_routes`).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response, params
from pydantic import ValidationError

from chimera.api.design_schemas import (
    DesignAuditIn,
    DesignAuditOut,
    DesignCheckOut,
    DesignConfirmIn,
    DesignCreateIn,
    DesignDeletedOut,
    DesignDirectionsIn,
    DesignExportIn,
    DesignOpsIn,
    DesignOpsOut,
    DesignProposalOut,
    DesignProposeIn,
    DesignRefineIn,
    DesignRestoreIn,
    DesignSummaryOut,
    DesignVersionOut,
)
from chimera.config import Settings
from chimera.design.exports import EXPORT_FORMATS, EXPORT_MEDIA_TYPES
from chimera.design.model import DesignDocument, DesignError, DesignTokens, wire
from chimera.design.ops import DesignOperationError, parse_operations, short_errors
from chimera.design.store import DEFAULT_TITLE, DesignConflict, DesignNotFound, DesignStore
from chimera.telemetry import get_logger

_log = get_logger("api.design")

#: The usage label of every model call made here, so the Cost screen can tell design spend apart.
USAGE_LABEL = "design"
#: Constraints are a small object of hints (format, audience, sizes); this keeps a pasted document
#: from riding into the prompt through them.
MAX_CONSTRAINTS_CHARS = 4_000


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="no such design")


def register_design_api(
    app: FastAPI,
    guard: params.Depends,
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
    backend_factory: Callable[[], Any] | None = None,
) -> None:
    """Mount ``/api/design``.

    ``home`` is read from ``settings`` (the launch snapshot), like every route that stores files.
    ``backend_factory`` is injectable so the model routes can be tested without a provider key; the
    default is the app's configured gateway.
    """
    read_settings = live_settings or (lambda: settings)
    home = Path(settings.home)
    store = DesignStore(home / "designs")
    audit = _Audit(home / "audit.jsonl")

    def load(design_id: str) -> DesignDocument:
        try:
            return store.get(design_id)
        except DesignNotFound:
            raise _not_found() from None
        except DesignError as exc:
            _log.warning("design %s unreadable: %s", design_id, exc)
            raise HTTPException(status_code=500, detail="the design could not be read") from None

    @app.get("/api/design", dependencies=[guard], response_model=list[DesignSummaryOut])
    def list_designs() -> list[dict[str, Any]]:
        """Every readable design, most recently updated first."""
        return [asdict(s) for s in store.list_designs()]

    @app.post("/api/design", dependencies=[guard], response_model=dict[str, Any])
    def create_design(body: DesignCreateIn) -> dict[str, Any]:
        """A new, empty design at revision 1. The id is generated here; the client never names it."""
        tokens: DesignTokens | None = None
        if body.tokens is not None:
            try:
                tokens = DesignTokens.model_validate(body.tokens)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=f"tokens: {short_errors(exc)}") from None
        try:
            with _saving():
                doc = store.create(
                    body.title or DEFAULT_TITLE, tokens=tokens, design_system_id=body.design_system_id
                )
        except DesignError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        audit.record("design.create", {"design_id": doc.id, "revision": doc.revision})
        return wire(doc)

    @app.get("/api/design/{design_id}", dependencies=[guard], response_model=dict[str, Any])
    def read_design(design_id: str) -> dict[str, Any]:
        """The document in the wire format (camelCase, as `lib/design/model.ts`)."""
        return wire(load(design_id))

    @app.post("/api/design/{design_id}/ops", dependencies=[guard], response_model=DesignOpsOut)
    def apply_ops(design_id: str, body: DesignOpsIn) -> dict[str, Any]:
        """Apply a batch of operations whole, or not at all; 409 when the revision moved on."""
        from chimera.design.propose import MAX_PROPOSAL_OPS

        load(design_id)  # 404 / unreadable, before the batch is judged
        try:
            operations = parse_operations(body.operations)
        except DesignOperationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        proposal = body.origin == "proposal"
        if proposal and len(operations) > MAX_PROPOSAL_OPS:
            raise HTTPException(
                status_code=422, detail=f"a proposal holds at most {MAX_PROPOSAL_OPS} operations"
            )
        try:
            with _saving():
                saved, inverse = store.apply(
                    design_id,
                    operations,
                    expected_revision=body.expected_revision,
                    allow_lock_changes=not proposal,
                )
        except DesignConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except DesignNotFound:
            raise _not_found() from None
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=short_errors(exc)) from None
        except DesignError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        audit.record(
            "design.ops",
            {
                "design_id": design_id,
                "revision": saved.revision,
                "operations": len(operations),
                "types": sorted({op.type for op in operations}),
                "origin": body.origin,
            },
        )
        return {"document": wire(saved), "inverse": [wire(op) for op in inverse]}

    @app.get(
        "/api/design/{design_id}/versions",
        dependencies=[guard],
        response_model=list[DesignVersionOut],
    )
    def list_versions(design_id: str) -> list[dict[str, Any]]:
        """Saved revisions, newest first."""
        load(design_id)
        return [asdict(v) for v in store.versions(design_id)]

    @app.post("/api/design/{design_id}/restore", dependencies=[guard], response_model=dict[str, Any])
    def restore_version(design_id: str, body: DesignRestoreIn) -> dict[str, Any]:
        """Make an old revision's content the newest revision. History is never rewritten."""
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="this replaces what is on the canvas; send confirm: true to restore",
            )
        current = load(design_id)
        expected = current.revision if body.expected_revision is None else body.expected_revision
        try:
            with _saving():
                saved = store.restore(design_id, body.revision, expected_revision=expected)
        except DesignConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except DesignNotFound:
            raise HTTPException(
                status_code=404, detail=f"this design has no saved revision {body.revision}"
            ) from None
        except DesignError as exc:
            _log.warning("restore of %s r%d failed: %s", design_id, body.revision, exc)
            raise HTTPException(status_code=422, detail="that revision could not be restored") from None
        audit.record(
            "design.restore",
            {"design_id": design_id, "from_revision": body.revision, "revision": saved.revision},
        )
        return wire(saved)

    @app.delete("/api/design/{design_id}", dependencies=[guard], response_model=DesignDeletedOut)
    def delete_design(
        design_id: str,
        body: DesignConfirmIn | None = None,
        confirm: bool = Query(default=False),
    ) -> dict[str, bool]:
        """Remove a design and its whole history. ``confirm: true`` in the body or the query."""
        if not (confirm or (body is not None and body.confirm)):
            raise HTTPException(
                status_code=400,
                detail="this deletes the design and its history; send confirm: true to do it",
            )
        load(design_id)
        try:
            with _saving():
                deleted = store.delete(design_id)
        except DesignNotFound:
            raise _not_found() from None
        if not deleted:
            raise _not_found()
        audit.record("design.delete", {"design_id": design_id})
        return {"deleted": True}

    @app.get("/api/design/{design_id}/check", dependencies=[guard], response_model=DesignCheckOut)
    def check_design(design_id: str) -> dict[str, Any]:
        """The deterministic checks against the document's own tokens. No model is called."""
        from chimera.design.export_html import check_against_tokens

        doc = load(design_id)
        return {"revision": doc.revision, "findings": [_finding(f) for f in check_against_tokens(doc)]}

    # ---- the model calls ----------------------------------------------------------------------

    @contextmanager
    def metered(design_id: str, action: str) -> Iterator[Any]:
        """The configured backend behind a meter labelled "design"; one usage row on the way out."""
        from chimera.orchestration.metering import MeteredBackend

        if backend_factory is None and not read_settings().can_answer():
            raise HTTPException(status_code=409, detail="no model is configured to answer")
        if backend_factory is not None:
            inner = backend_factory()
        else:
            from chimera.providers import LLMGateway

            inner = LLMGateway()
        meter = MeteredBackend(inner, label=USAGE_LABEL)
        try:
            yield meter
        except HTTPException:
            raise
        except Exception as exc:
            from chimera.providers import MissingCredentialsError

            if isinstance(exc, MissingCredentialsError):
                raise HTTPException(status_code=409, detail="no model is configured to answer") from None
            _log.warning("design %s for %s failed: %s", action, design_id, type(exc).__name__)
            raise HTTPException(status_code=502, detail="the model call failed; try again") from None
        finally:
            _record_spend(home, meter, design_id, action)

    def proposal_out(doc: DesignDocument, outcome: Any, meter: Any) -> dict[str, Any]:
        proposal = outcome.proposal
        out: dict[str, Any] = {
            "ok": outcome.ok,
            "base_revision": doc.revision,
            "error": outcome.error,
            "attempts": outcome.attempts,
            "usd": meter.usd,
            "preview": wire(outcome.preview) if outcome.preview is not None else None,
        }
        if proposal is not None:
            out.update(
                summary=proposal.summary,
                assumptions=list(proposal.assumptions),
                operations=[wire(op) for op in proposal.operations],
                checks=list(proposal.checks),
                questions=list(proposal.questions),
            )
        return out

    @app.post(
        "/api/design/{design_id}/propose", dependencies=[guard], response_model=DesignProposalOut
    )
    def propose_design(design_id: str, body: DesignProposeIn) -> dict[str, Any]:
        """A proposal for ``brief``, validated and dry-run against the document — never applied."""
        from chimera.design import propose as design_propose

        if len(json.dumps(body.constraints, ensure_ascii=False)) > MAX_CONSTRAINTS_CHARS:
            raise HTTPException(
                status_code=422, detail=f"constraints: at most {MAX_CONSTRAINTS_CHARS} characters"
            )
        doc = load(design_id)
        with metered(design_id, "propose") as meter:
            outcome = design_propose.propose(meter, doc, body.brief, body.constraints)
        audit.record("design.propose", _proposal_event(design_id, doc, outcome))
        return proposal_out(doc, outcome, meter)

    @app.post(
        "/api/design/{design_id}/directions", dependencies=[guard], response_model=DesignProposalOut
    )
    def directions_design(design_id: str, body: DesignDirectionsIn) -> dict[str, Any]:
        """Return 2–4 independently generated visual directions side by side; never applied."""
        from chimera.design import propose as design_propose

        if len(json.dumps(body.constraints, ensure_ascii=False)) > MAX_CONSTRAINTS_CHARS:
            raise HTTPException(
                status_code=422, detail=f"constraints: at most {MAX_CONSTRAINTS_CHARS} characters"
            )
        doc = load(design_id)
        with metered(design_id, "directions") as meter:
            outcome = design_propose.propose_directions(
                meter, doc, body.brief, body.count, body.constraints
            )
        audit.record("design.directions", _proposal_event(design_id, doc, outcome))
        return proposal_out(doc, outcome, meter)

    @app.post(
        "/api/design/{design_id}/refine", dependencies=[guard], response_model=DesignProposalOut
    )
    def refine_design(design_id: str, body: DesignRefineIn) -> dict[str, Any]:
        """A targeted change for ``request``, scoped by the selected elements — never applied."""
        from chimera.design import propose as design_propose

        doc = load(design_id)
        html_selection = None
        if body.html_selection is not None:
            from chimera.design import ops as design_ops
            from chimera.design.html_source import locate_html_element
            if not design_ops.HTML_ARTBOARDS_ENABLED:
                raise HTTPException(status_code=422, detail="HTML artboard selection is not available")
            found = next(
                ((page, board) for page in doc.pages for board in page.artboards
                 if board.id == body.html_selection.artboard_id and board.html is not None),
                None,
            )
            if found is None:
                raise HTTPException(status_code=422, detail="Selected HTML artboard was not found")
            try:
                offsets = locate_html_element(found[1].html or "", body.html_selection.index)
            except Exception as exc:
                raise HTTPException(status_code=422, detail="Selected HTML element was not found") from exc
            if offsets.tag != body.html_selection.tag:
                raise HTTPException(status_code=422, detail="Selected HTML element changed")
            html_selection = {"artboard_id": found[1].id, "index": body.html_selection.index, "tag": offsets.tag}
        with metered(design_id, "refine") as meter:
            outcome = design_propose.refine(
                meter, doc, body.request, body.selected_ids, html_selection=html_selection
            )
        event = _proposal_event(design_id, doc, outcome)
        event["selected"] = len(body.selected_ids)
        audit.record("design.refine", event)
        return proposal_out(doc, outcome, meter)

    @app.post("/api/design/{design_id}/audit", dependencies=[guard], response_model=DesignAuditOut)
    def audit_design(design_id: str, body: DesignAuditIn) -> dict[str, Any]:
        """The model's review against the brief, beside the deterministic token checks."""
        from chimera.design import propose as design_propose

        doc = load(design_id)
        with metered(design_id, "audit") as meter:
            outcome = design_propose.audit(meter, doc, body.brief)
        result = outcome.audit
        audit.record(
            "design.audit",
            {
                "design_id": design_id,
                "revision": doc.revision,
                "ok": result is not None,
                "issues": len(result.issues) if result is not None else 0,
                "findings": len(outcome.findings),
            },
        )
        out: dict[str, Any] = {
            "ok": result is not None,
            "revision": doc.revision,
            "findings": [_finding(f) for f in outcome.findings],
            "unverifiable": outcome.unverifiable,
            "error": outcome.error,
            "attempts": outcome.attempts,
            "usd": meter.usd,
        }
        if result is not None:
            out.update(
                issues=[
                    {"severity": i.severity, "node_id": i.node_id, "evidence": i.evidence, "fix": i.fix}
                    for i in result.issues
                ],
                passed_checks=list(result.passed_checks),
                needs_user_decision=list(result.needs_user_decision),
            )
        return out

    # ---- export -------------------------------------------------------------------------------

    @app.post(
        "/api/design/{design_id}/export",
        dependencies=[guard],
        response_class=Response,
        responses={
            200: {
                "content": {
                    media: {"schema": {"type": "string", "format": "binary"}}
                    for media in EXPORT_MEDIA_TYPES
                }
            }
        },
    )
    def export_design(design_id: str, body: DesignExportIn) -> Response:
        """The design as a file to save — a download, never a page this server shows.

        ``format`` is ``html``, ``pdf``, ``png`` (one artboard; a zip of PNGs for several) or
        ``pptx``. ``Content-Disposition: attachment`` so a browser saves it instead of rendering it
        on this origin, ``nosniff`` so the type is not guessed, and the exporter's own restrictive
        policy as a header too, for anything that does open it here. A format this machine cannot
        write (no Chromium for PDF/PNG, no ``documents-out`` extra for PPTX) is a 503 whose detail
        says what to install; nothing is downloaded on the person's behalf.
        """
        from chimera.design.export_html import CSP
        from chimera.design.exports import ExportFailed, ExportUnavailable, export_document

        fmt = body.format.strip().lower()
        if fmt not in EXPORT_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"format must be one of: {', '.join(EXPORT_FORMATS)}",
            )
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="this writes the design to a file that leaves the app; send confirm: true",
            )
        doc = load(design_id)
        # No asset store yet: images are drawn as labelled placeholders by every exporter.
        try:
            exported = export_document(doc, fmt)
        except ExportUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        except ExportFailed as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        audit.record(
            "design.export",
            {"design_id": design_id, "revision": doc.revision, "format": fmt,
             "bytes": len(exported.data)},
        )
        return Response(
            content=exported.data,
            media_type=exported.media_type,
            headers={
                # Built from the server-generated id alone: the title is the owner's text, and a
                # header is no place to echo it.
                "Content-Disposition": f'attachment; filename="design-{doc.id}.{exported.extension}"',
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": CSP,
                "Cache-Control": "no-store",
            },
        )


# ---- helpers ------------------------------------------------------------------------------------


@contextmanager
def _saving() -> Iterator[None]:
    """A disk that refuses a write answers with a sentence; the OS detail goes to the log."""
    try:
        yield
    except OSError as exc:
        _log.warning("design write failed: %s", exc)
        raise HTTPException(status_code=500, detail="the design could not be saved") from None


def _finding(issue: Any) -> dict[str, Any]:
    return {
        "kind": issue.kind,
        "node_id": issue.node_id,
        "artboard_id": issue.artboard_id,
        "message": issue.message,
    }


def _proposal_event(design_id: str, doc: DesignDocument, outcome: Any) -> dict[str, Any]:
    proposal = outcome.proposal
    return {
        "design_id": design_id,
        "revision": doc.revision,
        "ok": outcome.ok,
        "operations": len(proposal.operations) if proposal is not None else 0,
        "attempts": outcome.attempts,
    }


def _record_spend(home: Path, meter: Any, design_id: str, action: str) -> None:
    """One usage row for the calls ``meter`` saw, or none when no call returned."""
    if not meter.calls:
        return
    from chimera.api.usage import record_spend

    record_spend(
        home,
        session_id=f"{USAGE_LABEL}:{action}:{design_id}:{uuid4().hex[:8]}",
        model=meter.last_model,
        prompt_tokens=meter.prompt_tokens,
        completion_tokens=meter.completion_tokens,
        usd=meter.usd,
        route_kind=None,
    )


class _Audit:
    """One :class:`~chimera.governance.audit.AuditLog`, opened on first use.

    Not per request: opening one reads the whole file, and autosave sends ``/ops`` often. Sharing is
    safe because ``record`` re-reads the chain's tail from disk under its locks.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._log: Any = None
        self._lock = threading.Lock()

    def record(self, event: str, payload: dict[str, Any]) -> None:
        from chimera.governance.audit import AuditLog

        with self._lock:
            if self._log is None:
                self._log = AuditLog(self.path)
            log = self._log
        try:
            log.record(event, payload)
        except OSError as exc:
            # The change is saved; failing to record it must not turn into "not saved" on screen.
            _log.error("audit event %s not recorded: %s", event, exc)
