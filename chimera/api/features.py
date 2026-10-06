"""Feature endpoints for the desktop app — Memory, Skills, Cron, Tasks (M21 Fase C).

Each endpoint reuses an existing manager/store (the same one the matching ``chimera`` CLI command
builds), so the UI is a view over the real state, never a reimplementation. Reads and the HITL
approve/deny writes are pure file I/O — no live LLM call. The token-spending paths (running a project
step, executing a skill) are deliberately NOT exposed here; the app drives those through the
streaming chat / solve flows instead.

The exceptions are ``POST /api/memory/consolidate`` and ``POST /api/kanban/run``. The first merges
only the clusters the owner reviewed in a free preview, so its spend is a decision made on screen,
and it is metered onto the usage log like the CLI command it mirrors. The second is streamed for the
same reason the chat is: a board dispatch calls models for as long as it has cards, so it reports
each card as it lands rather than returning once at the end. Without it the board was a display case — every route it had was a
read, so the screen could show the work and change nothing about it.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Request, UploadFile, params
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from chimera.api.schemas import (
    ApprovedOut,
    ClaudeImportApplyOut,
    ClaudeImportPreviewOut,
    ConsolidateApplyOut,
    ConsolidatePreviewOut,
    CronCreateIn,
    CronJobOut,
    CronResultOut,
    CronSilenceOut,
    DeletedOut,
    KanbanCardIn,
    KanbanMoveIn,
    KanbanRunIn,
    LibraryCardOut,
    LibraryImportOut,
    MemoryAddOut,
    MemoryExportOut,
    MemoryItemOut,
    MemoryLayersOut,
    MemoryProfileOut,
    ProjectDetailOut,
    ProjectStartIn,
    ProjectStateOut,
    RetiredOut,
    SkillsOut,
    SpecDraftIn,
    SpecDraftOut,
    SpecWriteIn,
    SpecWriteOut,
    TaskCardOut,
    WeeklyReviewIn,
    WeeklyReviewOut,
)
from chimera.api.sse import SSE_RESPONSE
from chimera.config import get_settings
from chimera.telemetry import get_logger

_log = get_logger("api.features")

_MEMORY_KINDS = {"working", "episodic", "semantic", "persona"}


# --- serializers ----------------------------------------------------------------------------------
def _job_dict(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "trigger": job.trigger,
        "schedule": job.schedule,
        "action": job.action,
        "enabled": job.enabled,
        "next_run": job.next_run,
        "last_run": job.last_run,
        # The attempt and the outcome, side by side. `last_run` alone made a job that has failed on
        # every tick for a month read as one that just worked a minute ago.
        "last_status": job.last_status,
        "last_error": job.last_error,
        "consecutive_failures": job.consecutive_failures,
        # Which switch turned a disabled job off: the person, or the failure brake. Without it a
        # client can only show a braked job by its last error, which is the wrong fact — the brake
        # is the one that needs someone to switch the job back on. Read with a default: the store
        # holds jobs written before the field existed.
        "disabled_by": getattr(job, "disabled_by", "") or "",
        "created_by": job.created_by,
        "workspace": job.workspace,
        "deliver_to": job.deliver_to,
        # Echoed so a row can say whether this job is gated. Read off the job with a default rather
        # than assumed present: the store holds jobs written before these fields existed.
        "verify": getattr(job, "verify", "") or "",
        "max_attempts": int(getattr(job, "max_attempts", 1) or 1),
        "notify": getattr(job, "notify", "always") or "always",
        "tools": getattr(job, "tools", None),
    }


def _item_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "kind": item.kind,
        "provenance": item.provenance,
        "source": item.source,
        # Which folder this belongs to, or null for everywhere. On screen because the DEFAULT is
        # now to save into the project you are in: without it shown, a fact meant for every project
        # gets quietly filed under one and stops arriving, and nothing on the screen says why.
        "project": getattr(item, "project", None),
    }


def _card_dict(card: Any) -> dict[str, Any]:
    return {
        "id": card.id,
        "title": card.title,
        "action": card.action,
        "column": card.column,
        "success": card.success,
        "risk": card.metadata.get("risk"),
        "depends_on": card.metadata.get("depends_on", []),
        "lane": card.lane,
        "verify": card.verify,
        "result": card.result or "",
    }


def _state_dict(state: Any) -> dict[str, Any]:
    return {
        "id": state.id,
        "status": state.status,
        "iterations": state.iterations,
        "plan_approved": state.plan_approved,
        "pending_card_id": state.pending_card_id,
        "note": state.note,
        "max_iterations": state.max_iterations,
    }


# --- helpers --------------------------------------------------------------------------------------
# Every helper takes the settings it should read, rather than reaching for the process settings.
# `build_api_app(settings=...)` configures the app, and these endpoints used to ignore it and read
# `get_settings()` directly — so an injected settings object pointed the routes at the real user's
# home. Harmless in the product (the process settings ARE the real ones) and a trap in tests, which
# is where it was found: `test_the_scheduled_gate_is_reachable` wrote its job into the developer's
# own crontab and then read a different job back out of it. The caller now passes the settings it
# was built with; see `register_features`.
def _cron_store(settings: Any) -> Any:
    from chimera.scheduler import CronStore

    return CronStore(settings.home / "scheduler" / "jobs.json")


def _memory_manager(settings: Any) -> Any:
    from chimera.evolution.wiring import build_memory_manager

    return build_memory_manager(settings)


def _record_consolidation_spend(settings: Any, meter: Any, usage_id: str) -> None:
    """One usage row for the merges ``meter`` saw, or none when no call returned.

    The same row `chimera memory consolidate` writes (`cli.main._record_merge_spend`), kept here
    rather than imported because the API does not import the CLI module."""
    if not meter.calls:
        return
    from chimera.api.usage import record_spend

    record_spend(
        settings.home, session_id=usage_id, model=meter.last_model,
        prompt_tokens=meter.prompt_tokens, completion_tokens=meter.completion_tokens,
        usd=meter.usd, route_kind=None,
    )


def _skill_store(settings: Any) -> Any:
    from chimera.evolution import SkillStore

    return SkillStore(settings.home / "skills.json")


def _library_card_dict(card: Any, *, owned: set[str], body: str = "") -> dict[str, Any]:
    m = card.manifest
    return {
        "name": m.name,
        "description": m.description,
        "version": m.version,
        "kind": m.kind,
        "stage": m.stage,
        "topic": m.topic,
        "triggers": list(m.triggers),
        "license": m.license,
        "body": body,
        "imported": m.name in owned,
    }


def _load_project(project_id: str, settings: Any) -> Any:
    """Rebuild the orchestrator from disk for an HITL write. Constructing the solve lane makes NO
    model call — the LLM only runs inside step()/run(), which the approve/deny endpoints never call.
    """
    from chimera.kanban.lanes import SolveLane
    from chimera.orchestration.project import ProjectOrchestrator, ProjectState

    home = settings.home
    state_path = ProjectOrchestrator.project_dir(home, project_id) / "project.json"
    if not state_path.exists():
        raise HTTPException(status_code=404, detail="project not found")
    state = ProjectState.load(state_path)
    lane = SolveLane(workspace=Path(state.workspace), model=None)
    return ProjectOrchestrator.load(home, project_id, solve_card=lane)


class MemoryAdd(BaseModel):
    content: str
    kind: str = "semantic"
    key: str | None = None
    #: Which project this fact belongs to. Omitted means everywhere, which is what a fact
    #: typed into the Memory screen usually is — the owner stating something, rather than
    #: the agent noting what it learned inside one folder.
    project: str | None = None


class MemoryEdit(BaseModel):
    content: str


class ClaudeImportPreviewIn(BaseModel):
    #: The folder to read. Omitted means ``~/.claude``, where Claude keeps the global CLAUDE.md and
    #: every project's auto-memory.
    path: str | None = None


class ClaudeImportApplyIn(BaseModel):
    path: str | None = None
    #: The candidates the owner ticked, as the preview returned them. Required and explicit: there
    #: is no "import everything" here, because the review IS the selection.
    contents: list[str] = Field(default_factory=list)


class ConsolidatePreviewIn(BaseModel):
    threshold: float = Field(default=0.5, ge=0.05, le=1.0)


class ConsolidateApplyIn(BaseModel):
    threshold: float = Field(default=0.5, ge=0.05, le=1.0)
    #: The reviewed clusters, each as the ids the preview listed. Only an exact match is merged.
    groups: list[list[str]] = Field(default_factory=list)


class ApproveBody(BaseModel):
    card: str | None = None


class CatalogEntryOut(BaseModel):
    """One installable skill, and everything a person needs before deciding to download it."""

    name: str = ""
    description: str = ""
    topic: str = ""
    license: str = ""
    permissive: bool = Field(
        default=False,
        description="Whether the licence is one that needs no further reading before use.",
    )
    portability: str = Field(
        default="",
        description=(
            "native | needs_setup | needs_service | needs_heavy | os_locked | needs_adaptation — "
            "these were written for another agent, and most do not transfer unchanged."
        ),
    )
    requires: list[str] = Field(default_factory=list)
    note: str = ""
    author: str = ""
    homepage: str = ""
    missing_tools: list[str] = Field(
        default_factory=list,
        description=(
            "Tool names this skill's text calls that nothing here provides. MEASURED from the "
            "body, unlike `portability` which is a judgement — a mention is not always a "
            "dependency, so this informs rather than decides."
        ),
    )
    #: What is on this machine already: "" when not installed, else pending/active/inactive.
    installed: str = ""


class BundleOut(BaseModel):
    name: str = ""
    description: str = ""
    status: str = Field(default="", description="pending | active | inactive | unknown")
    license: str = ""
    source: str = ""
    ref: str = ""
    installed_at: str = ""
    files: list[str] = Field(default_factory=list)
    committed_at: str = Field(
        default="",
        description="When the commit in `ref` was made, as the source reported it at install.",
    )
    reconfirm: bool = Field(
        default=False,
        description=(
            "The bundle was switched on while a switched-on bundle reached no prompt; it reads as "
            "`pending` and reaches nothing until switched on again."
        ),
    )
    origin: str = Field(default="catalog", description="catalog | upload — how it arrived.")
    provenance: str = Field(
        default="tainted",
        description="Always tainted: every bundle is somebody else's text and scripts.",
    )


class EffectiveBundleOut(BaseModel):
    name: str = ""
    description: str = ""
    ref: str = ""
    committed_at: str = ""


class EffectiveSkillsOut(BaseModel):
    """What a run started now is told about skills — read from the code that tells it."""

    bundles: list[EffectiveBundleOut] = Field(default_factory=list)
    bundle_text: str = Field(
        default="",
        description=(
            "The installed-skills block byte for byte as a run's prompt carries it "
            "(`bundles.prompt_block`), or empty when no bundle is switched on."
        ),
    )
    reconfirm: list[str] = Field(
        default_factory=list,
        description=(
            "Bundles switched on while a switched-on bundle reached no prompt (before study 29, "
            "P7.1). They reach nothing until switched on again, and are named here so the change "
            "is seen where the prompt's skills are."
        ),
    )
    cards_read: bool = Field(
        default=False,
        description=(
            "`CHIMERA_SKILL_CARDS`. Off, no learned card reaches any prompt whatever its status."
        ),
    )
    cards: list[str] = Field(
        default_factory=list,
        description=(
            "Learned cards the retriever may choose from (active and provisional). Which of them "
            "a run reads depends on its task, at most `cards_k` per run; empty when reading is off."
        ),
    )
    cards_k: int = 0


class BundleUpdateOut(BaseModel):
    name: str = ""
    current_ref: str = ""
    current_date: str = ""
    latest_ref: str = Field(
        default="", description="The newest commit that touched the skill's own directory."
    )
    latest_date: str = ""
    changed: bool | None = Field(
        default=None,
        description="Whether that commit is newer than the installed one; null when unknowable.",
    )


class BundleTextOut(BaseModel):
    """An installed skill's SKILL.md, as text, for the owner to read before switching it on."""

    name: str
    text: str
    truncated: bool = False


#: FastAPI's upload markers, hoisted out of the signature so a call in an argument default does not
#: trip the linter — the same arrangement `code_api` uses for attachments.
_SKILL_FILES = File(..., description="One .zip, one SKILL.md, or every file of a skill folder.")
_SKILL_PATHS = Form(
    default=[],
    description=(
        "For a folder: each file's path inside it, in the same order as `files`. A browser does "
        "not send a picked folder's structure in the file name, so it travels beside it."
    ),
)


def _cross_site(request: Request, named: Callable[[], list[str]]) -> bool:
    """Whether a browser sent this request from a page that is not this app.

    Without a server token (the desktop default) the bearer guard is a no-op, and a multipart POST
    is a CORS "simple request": no preflight, so any page open in the owner's browser can send one
    to 127.0.0.1 and the browser only withholds the ANSWER. For an upload the answer is not the
    point — the skill would already be on disk, or an approved one replaced. Browsers say where a
    request came from (`Origin`, and `Sec-Fetch-Site` in current ones); a client that is not a
    browser sends neither and is not what this defends against.

    Allowed: no Origin at all, this app's own origin, or an origin the operator named in
    ``CHIMERA_ALLOWED_ORIGINS`` (the desktop pointed at this instance from another machine).

    "Own" is built from the request's ``Host``, which a page that rebound its DNS name to
    127.0.0.1 controls — its Origin then matches it exactly and ``Sec-Fetch-Site`` says
    same-origin. So the own origin counts only when its host is one no DNS answer stands behind
    (an IP literal or ``localhost``, :func:`chimera.api.host_guard.unrebindable`). The app-wide
    :class:`~chimera.api.host_guard.LoopbackHostGuard` refuses such a Host first on a loopback
    bind; this holds on any bind, for the one route where an answer is not needed to do harm.
    """
    from chimera.api.host_guard import unrebindable

    origin = request.headers.get("origin")
    own = f"{request.url.scheme}://{request.url.netloc}"
    is_own = origin == own and unrebindable(request.url.hostname)
    allowed = origin is not None and (is_own or origin in named())
    if origin is not None and not allowed:
        return True
    fetch_site = request.headers.get("sec-fetch-site", "")
    # `same-site` is still another page: a dev server on 127.0.0.1:3000 is the same site as :8765.
    return fetch_site in ("cross-site", "same-site") and not allowed


def _upload_route(named: Callable[[], list[str]], max_body: int) -> type[APIRoute]:
    """A route class that judges the request BEFORE FastAPI reads its body.

    FastAPI parses a `File`/`Form` body before dependencies run and before the handler is called —
    the whole multipart lands in temporary files first — so a dependency is too late to refuse
    anything about the body. The route's own handler wrapper is the first code that sees the
    request.
    """

    class UploadRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
            handler = super().get_route_handler()

            async def judged(request: Request) -> Response:
                if _cross_site(request, named):
                    return JSONResponse(
                        {"detail": "refusing an upload sent from another site"}, status_code=403
                    )
                # By what the request DECLARES, because the alternative is reading it: the form
                # parser spools every part to a temporary file (to disk past 1 MB) before the
                # handler's own per-file bound runs, so a 5 GB body would be on the owner's disk
                # before anything here could say no. A browser always declares the length of a
                # FormData body; a client that will not declare it is refused rather than trusted.
                declared = request.headers.get("content-length", "")
                if not declared.isdigit():
                    return JSONResponse(
                        {"detail": "an upload has to say how large it is (Content-Length)"},
                        status_code=411,
                    )
                if int(declared) > max_body:
                    return JSONResponse(
                        {"detail": f"the upload is larger than the {max_body // 1024 // 1024}MB limit"},
                        status_code=413,
                    )
                return await handler(request)

            return judged

    return UploadRoute


class BundleStatusIn(BaseModel):
    status: str = Field(default="active", description="active | inactive")


def register_features(
    app: FastAPI,
    guard: params.Depends,
    *,
    workspace: Path | None = None,
    settings: Any = None,
) -> None:
    """Attach the Fase C feature routes to ``app``. ``guard`` enforces the bearer token on mutations.

    ``workspace`` is where a dispatched card works when the request names none. Optional so every
    existing caller keeps working; absent, it falls back to the process directory, which is what the
    board did before it could be dispatched from here at all.

    ``settings`` is the settings the app was built with. Absent, the routes read the process settings
    — which is what they always did, and correct for the product. Passing one means "use THIS", the
    same contract ``build_api_app`` states for its own ``settings`` argument, and it is what stops a
    test from writing into the developer's real home. Read through a callable rather than captured,
    so a route that runs after ``PATCH /api/config`` sees the change, exactly as ``live_settings()``
    in ``app.py`` does.
    """
    default_workspace = workspace or Path.cwd()

    def _settings() -> Any:
        return settings or get_settings()

    # ---- Memory -----------------------------------------------------------------------------------
    @app.get("/api/memory", dependencies=[guard], response_model=list[MemoryItemOut])
    def list_memory(q: str = "", k: int = 30) -> list[dict[str, Any]]:
        k = max(1, min(k, 200))  # clamp: a negative/huge k must not dump the whole store
        mgr = _memory_manager(_settings())
        items = mgr.search(q, k=k) if q.strip() else mgr.store.all()
        return [_item_dict(it) for it in items]

    @app.get("/api/memory/layers", dependencies=[guard], response_model=MemoryLayersOut)
    def memory_layers() -> dict[str, Any]:
        from chimera.api.memory_layers import summarize_memory_layers

        settings = _settings()
        return summarize_memory_layers(
            _memory_manager(_settings()).store.all(),
            semantic_embeddings_enabled=settings.semantic_memory,
        )

    @app.get("/api/memory/profile", dependencies=[guard], response_model=MemoryProfileOut)
    def memory_profile() -> dict[str, Any]:
        mgr = _memory_manager(_settings())
        return {
            # The FACTS, not the prompt block. `profile()` opens with "What you know about the
            # user:" — an instruction addressed to a model — and the screen was rendering that
            # verbatim under an already-translated panel heading, in an app translated into ten
            # languages. The screen supplies its own heading; this supplies what goes under it.
            "profile": '\n'.join(mgr.profile_facts()),
            "persona": [_item_dict(it) for it in mgr.store.by_kind("persona")],
        }

    @app.post("/api/memory", dependencies=[guard], response_model=MemoryAddOut)
    def add_memory(body: MemoryAdd) -> dict[str, Any]:
        if body.kind not in _MEMORY_KINDS:
            raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(_MEMORY_KINDS)}")
        mgr = _memory_manager(_settings())
        # `project` omitted means everywhere, and that is right for this route: a fact typed
        # by hand into the Memory screen is the owner stating something, not the agent noting
        # what it learned while working in a folder.
        status, item = mgr.remember(
            body.content, body.kind, key=body.key, project=body.project
        )
        return {"status": status, "item": _item_dict(item)}

    @app.delete("/api/memory/{item_id}", dependencies=[guard], response_model=DeletedOut)
    def delete_memory(item_id: str) -> dict[str, bool]:
        _memory_manager(_settings()).delete(item_id)
        return {"deleted": True}

    # None of the routes below is in the desktop bridge's table (`bridge_routes.ROUTES`), and that is
    # the decision, not an omission: rewriting a fact, reading another tool's notes into memory and
    # spending tokens to merge facts are things the owner does from the screen, not things an agent
    # driving the app should be able to do on its own.
    @app.put("/api/memory/{item_id}", dependencies=[guard], response_model=MemoryItemOut)
    def edit_memory(item_id: str, body: MemoryEdit) -> dict[str, Any]:
        mgr = _memory_manager(_settings())
        try:
            item = mgr.edit(item_id, body.content)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="memory not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _item_dict(item)

    @app.get("/api/memory/export", dependencies=[guard], response_model=MemoryExportOut)
    def export_memory_file(format: str = "json") -> dict[str, Any]:  # noqa: A002 — the query name
        from datetime import UTC, datetime

        from chimera.memory.export import EXPORT_FORMATS, export_memory

        if format not in EXPORT_FORMATS:
            raise HTTPException(status_code=400, detail=f"format must be one of {list(EXPORT_FORMATS)}")
        items = _memory_manager(_settings()).store.all()
        now = datetime.now(UTC)
        stamp = now.strftime("%Y%m%d-%H%M%S")
        ext, media = ("json", "application/json") if format == "json" else ("md", "text/markdown")
        return {
            "format": format,
            "filename": f"chimera-memory-{stamp}.{ext}",
            "media_type": media,
            "count": len(items),
            "content": export_memory(items, format, exported_at=now.isoformat(timespec="seconds")),
        }

    def _claude_importer(path: str | None) -> Any:
        from chimera.migration import ClaudeImporter
        from chimera.migration.importers import registered_project_keys

        folder = Path(path).expanduser() if path and path.strip() else Path.home() / ".claude"
        if not folder.is_dir():
            raise HTTPException(status_code=400, detail="not a folder")
        # The registered folders, so a note from one repository's Claude memory is filed under that
        # repository instead of being recalled everywhere (see ClaudeImporter).
        return ClaudeImporter(folder, projects=registered_project_keys(_settings().home))

    @app.post(
        "/api/memory/import/claude/preview",
        dependencies=[guard],
        response_model=ClaudeImportPreviewOut,
    )
    def preview_claude_import(body: ClaudeImportPreviewIn) -> dict[str, Any]:
        from chimera.memory.manager import _normalize

        importer = _claude_importer(body.path)
        result = importer.scan()
        known = {_normalize(i.content) for i in _memory_manager(_settings()).store.all()}
        return {
            "path": str(importer.home),
            "files": result.memory_files,
            "candidates": [
                {
                    "content": item.content,
                    "file": str(item.metadata.get("file", "")),
                    "known": _normalize(item.content) in known,
                    # Where the fact will apply, shown BEFORE the write: "everywhere" was only
                    # visible as a badge after the import, when the fact was already being recalled.
                    "project": item.project,
                    "claude_project": str(item.metadata.get("claude_project", "")),
                }
                for item in importer.memory_items()
            ],
            "notes": result.notes,
        }

    @app.post(
        "/api/memory/import/claude/apply",
        dependencies=[guard],
        response_model=ClaudeImportApplyOut,
    )
    def apply_claude_import(body: ClaudeImportApplyIn) -> dict[str, Any]:
        importer = _claude_importer(body.path)
        chosen = set(body.contents)
        if not chosen:
            raise HTTPException(status_code=400, detail="choose the facts to import")
        candidates = set(importer.scan().candidates)
        result = importer.apply(
            _settings().home, memory_manager=_memory_manager(_settings()), only=chosen
        )
        counts = result.memory_merged or {}
        return {
            "written": sum(counts.values()),
            "ignored": len(chosen - candidates),
            "counts": counts,
        }

    @app.post(
        "/api/memory/consolidate/preview",
        dependencies=[guard],
        response_model=ConsolidatePreviewOut,
    )
    def preview_consolidation(body: ConsolidatePreviewIn) -> dict[str, Any]:
        settings = _settings()
        groups = _memory_manager(settings).consolidation_groups(threshold=body.threshold)
        return {
            "groups": [
                {
                    "kind": group[0].kind,
                    "project": group[0].project,
                    "unverified": any(i.provenance == "tainted" for i in group),
                    "items": [_item_dict(i) for i in group],
                }
                for group in groups
            ],
            "can_answer": bool(settings.can_answer()),
        }

    @app.post("/api/memory/consolidate", dependencies=[guard], response_model=ConsolidateApplyOut)
    def apply_consolidation(body: ConsolidateApplyIn) -> dict[str, Any]:
        """Merge the clusters the owner reviewed. The one memory route that calls a model.

        Each merge is a model call, metered and written to the usage log as a row of its own —
        the same accounting `chimera memory consolidate` keeps, so the Cost screen sees it.
        """
        from uuid import uuid4

        from chimera.memory import consolidate as consolidation
        from chimera.orchestration.metering import MeteredBackend
        from chimera.providers import LLMGateway, MissingCredentialsError

        reviewed = {frozenset(g) for g in body.groups if len(g) >= 2}
        if not reviewed:
            raise HTTPException(status_code=400, detail="choose the groups to merge")
        settings = _settings()
        if not settings.can_answer():
            raise HTTPException(status_code=409, detail="no model is configured to write the merge")
        mgr = _memory_manager(settings)
        meter = MeteredBackend(LLMGateway(), label="consolidate")
        usage_id = f"consolidate:{uuid4().hex[:12]}"
        try:
            outcome = mgr.consolidate_outcome(
                consolidation.model_summarizer(meter), threshold=body.threshold, only=reviewed
            )
        except MissingCredentialsError as exc:
            raise HTTPException(status_code=409, detail="no model is configured to write the merge") from exc
        finally:
            # On the way out whatever happened: a run that failed part-way paid for its merges.
            _record_consolidation_spend(settings, meter, usage_id)
        # Counted from what the merge DID, not from which reviewed groups still matched: a group the
        # model answered with nothing is left as it was, and was reported "merged" — for a call
        # that was paid for and changed nothing.
        return {
            "merged": outcome.merged,
            "skipped": outcome.blank,
            "stale": len(reviewed) - outcome.merged - outcome.blank,
            "removed": outcome.removed,
            "usd": meter.usd,
        }

    # ---- Skills -----------------------------------------------------------------------------------
    @app.get("/api/skills", dependencies=[guard], response_model=SkillsOut)
    def list_skills() -> dict[str, Any]:
        store = _skill_store(_settings())
        return {
            "stats": store.stats_overview(),
            "retirement_candidates": store.retirement_candidates_any_context(),
            # Live, not the process default: this is a setting a person can change, and a screen
            # explaining why a count is zero must explain the state the app is actually in.
            "cards_read": bool(getattr(_settings(), "skill_cards", False)),
        }

    @app.post("/api/skills/{name}/approve", dependencies=[guard], response_model=ApprovedOut)
    def approve_skill(name: str) -> dict[str, bool]:
        if not _skill_store(_settings()).approve(name):
            raise HTTPException(status_code=404, detail="skill not found")
        return {"approved": True}

    @app.post("/api/skills/{name}/retire", dependencies=[guard], response_model=RetiredOut)
    def retire_skill(name: str) -> dict[str, bool]:
        if not _skill_store(_settings()).retire(name):
            raise HTTPException(status_code=404, detail="skill not found")
        return {"retired": True}

    # ---- Installable skills from the wider ecosystem -----------------------------------------
    # A different SHAPE of skill from everything above, not a longer list of the same one. A card
    # is text that goes in the prompt; these are directories that ship scripts and reference files,
    # so they live on disk and what reaches the prompt is a name and a path. See
    # `chimera/skills/bundles.py` for why the store could not hold them.
    def _bundle_dicts() -> dict[str, str]:
        from chimera.skills.bundles import installed as installed_bundles

        return {b.name: b.status for b in installed_bundles(_settings().home)}

    @app.get("/api/skills/catalog", dependencies=[guard], response_model=list[CatalogEntryOut])
    def list_catalog() -> list[dict[str, Any]]:
        """The installable skills, with what each one needs said next to its name.

        Not vendored and not fetched here: this is the curated pointer list, and the licence and
        portability travel with every row because a flat list of eighty names would advertise
        eighty working features and deliver rather fewer.
        """
        from chimera.skills.catalog import CATALOG, license_is_permissive

        here = _bundle_dicts()
        return [
            CatalogEntryOut(
                name=e.name,
                description=e.description,
                topic=e.topic,
                license=e.license,
                permissive=license_is_permissive(e.license),
                portability=e.portability.value,
                requires=list(e.requires),
                note=e.note,
                author=e.author,
                homepage=e.homepage,
                missing_tools=list(e.missing),
                installed=here.get(e.name, ""),
            ).model_dump()
            for e in CATALOG
        ]

    @app.get("/api/skills/bundles", dependencies=[guard], response_model=list[BundleOut])
    def list_bundles() -> list[dict[str, Any]]:
        """What is installed on this machine, read from the disk rather than from an index."""
        from chimera.skills.bundles import installed as installed_bundles

        return [BundleOut(**b.to_dict()).model_dump() for b in installed_bundles(_settings().home)]

    @app.get("/api/skills/effective", dependencies=[guard], response_model=EffectiveSkillsOut)
    def effective_skills(project: str = "") -> dict[str, Any]:
        """What a run started now would be told about skills, read from the code that tells it.

        "Mine" was spread over three panels — learned cards, the library, the catalogue — and none
        of them answered the one question that matters when a run behaves oddly: what did the agent
        actually get? The bundle text is `prompt_block`, the function the agent itself calls, so the
        screen cannot show a list the prompt does not carry. Cards are task-dependent: a run reads
        at most `cards_k` of the eligible ones, and only with reading on — which is off by default,
        and said so here rather than left for a count of zero to suggest otherwise.

        ``project`` is the folder a run would start in. When its pack applies (study 29, P7.6) the
        bundles are narrowed exactly as an app run there is narrowed — the same two functions
        `assemble_registry` calls. The Skills screen itself sends no project (it belongs to none),
        so it shows the whole home and says so; and built-in skills, retrieved per task by
        `Agent._skill_context`, are not listed here, because which ones match depends on the task.
        """
        from chimera.core.project_pack import applied_pack, bundle_filter
        from chimera.skills.bundles import active, installed, prompt_block

        settings = _settings()
        home = settings.home
        only = bundle_filter(applied_pack(settings, project.strip() or None))
        cards_read = bool(getattr(settings, "skill_cards", False))
        cards = [c.name for c in _skill_store(settings).retrievable()] if cards_read else []
        return EffectiveSkillsOut(
            bundles=[
                EffectiveBundleOut(
                    name=b.name, description=b.description, ref=b.ref, committed_at=b.committed_at
                )
                for b in active(home)
                if only is None or b.name in only
            ],
            bundle_text=prompt_block(home, only=only),
            reconfirm=[b.name for b in installed(home) if b.reconfirm],
            cards_read=cards_read,
            cards=cards,
            cards_k=int(getattr(settings, "skill_cards_k", 0)) if cards_read else 0,
        ).model_dump()

    @app.get(
        "/api/skills/bundles/{name}/update", dependencies=[guard], response_model=BundleUpdateOut
    )
    def check_bundle_update(name: str) -> dict[str, Any]:
        """Ask the skill's source whether its directory changed since it was installed.

        One request to the host install already uses, made only when a person clicks. It changes
        nothing: updating is `POST /api/skills/catalog/{name}/install?force=true`, which lands the
        new files `pending` like any install — new instructions are a new decision.
        """
        from chimera.skills.bundles import BundleError, check_update
        from chimera.skills.catalog import find

        entry = find(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no skill named {name!r} in the catalogue")
        try:
            result = check_update(entry, _settings().home)
        except BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return BundleUpdateOut(**result.to_dict()).model_dump()

    @app.post("/api/skills/catalog/{name}/install", dependencies=[guard], response_model=BundleOut)
    def install_bundle(name: str, force: bool = False) -> dict[str, Any]:
        """Download one skill from its source repository. Runs nothing, and enables nothing.

        The bundle lands `pending`: its files are on disk and no part of it reaches a prompt until
        somebody switches it on. These are instructions written by a stranger, and an instruction
        in the system prompt has the standing of one the owner wrote — so downloading is not
        consenting, and it is a separate click.
        """
        from chimera.skills.bundles import BundleError, install
        from chimera.skills.catalog import find

        entry = find(name)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"no skill named {name!r} in the catalogue")
        try:
            record = install(entry, _settings().home, force=force)
        except BundleError as exc:
            # The message is written to be read by a person: which limit, which file, which host.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return BundleOut(**record.to_dict()).model_dump()

    # On its own router only for the route class: the cross-site and size checks have to run before
    # the body is parsed, and a route class is the one place that happens. Included right after, so
    # the route keeps its place in the table and in the OpenAPI document.
    def _named_origins() -> list[str]:
        raw = str(getattr(_settings(), "allowed_origins", "") or "")
        return [o.strip() for o in raw.split(",") if o.strip()]

    from chimera.skills.bundles import MAX_TOTAL_BYTES as _MAX_SKILL_BYTES

    # The files may total the skill limit; the rest is multipart framing — a few hundred bytes of
    # headers per part, at most 200 files — and the `paths` field. One megabyte covers it many
    # times over and still stops a body that is plainly not a skill.
    uploads = APIRouter(route_class=_upload_route(_named_origins, _MAX_SKILL_BYTES + 1024 * 1024))

    @uploads.post("/api/skills/import", dependencies=[guard], response_model=BundleOut)
    async def import_skill(
        files: list[UploadFile] = _SKILL_FILES,
        paths: list[str] = _SKILL_PATHS,
        replace: bool = False,
    ) -> dict[str, Any]:
        """Add a skill that is in no catalogue — the owner's own, or one found somewhere.

        Lands `pending` and `tainted` whatever its file declares — the rule a card imported by path
        follows for its labels: handing a file to the app is choosing to send it, not vouching for
        what it says. Its instructions reach no prompt until the owner switches it on; after that,
        `tainted` means what `skill_view` reads from it is marked untrusted and its description
        enters the prompt quoted and attributed to its author (`bundles._context_line`).
        Every limit the catalogue install has applies, and an archive is read as hostile input —
        see `chimera/skills/bundle_upload.py`. 409 when the name is taken and `replace` was not
        asked for, so the screen can offer the replacement instead of only reporting a failure.
        """
        from chimera.skills.bundle_upload import import_upload
        from chimera.skills.bundles import MAX_TOTAL_BYTES, BundleError, BundleExists, SwapNotUndone

        named = len(paths) == len(files)
        received: list[tuple[str, bytes]] = []
        total = 0
        for index, upload in enumerate(files):
            # Bounded per read: the whole skill may not exceed this, so no one file may either. This
            # is the bound on the FILES; the bound on the body — the one that runs before anything
            # is spooled — is the route class's Content-Length check above.
            data = await upload.read(MAX_TOTAL_BYTES + 1)
            total += len(data)
            if total > MAX_TOTAL_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"the upload is larger than the {MAX_TOTAL_BYTES // 1024 // 1024}MB limit",
                )
            received.append(((paths[index] if named else upload.filename) or "SKILL.md", data))
        label = received[0][0].split("/", 1)[0] if received else ""
        try:
            record = await run_in_threadpool(
                import_upload, received, _settings().home, replace=replace, label=label
            )
        except BundleExists as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SwapNotUndone as exc:
            # Before the generic answer below, which would say the old version is unchanged.
            raise HTTPException(
                status_code=500,
                detail=(
                    f"could not write the skill to disk ({exc.strerror}), and could not put the "
                    f"previous version of {exc.name!r} back either — it is kept aside, out of the "
                    "list, and is restored the next time a skill with that name is uploaded"
                ),
            ) from exc
        except OSError as exc:
            # The disk said no — on Windows most often a scanner holding a file that was just
            # written. A sentence instead of a bare 500; `_swap_into` has already put any previous
            # version back.
            raise HTTPException(
                status_code=500,
                detail=(
                    f"could not write the skill to disk ({exc.strerror or type(exc).__name__}) — "
                    "anything installed before is unchanged; try again"
                ),
            ) from exc
        return BundleOut(**record.to_dict()).model_dump()

    app.include_router(uploads)

    @app.get(
        "/api/skills/bundles/{name}/skill-md", dependencies=[guard], response_model=BundleTextOut
    )
    def read_bundle_text(name: str) -> dict[str, Any]:
        """The SKILL.md of an installed skill, as plain text — what the switch would consent to."""
        from chimera.skills.bundle_upload import read_skill_md

        found = read_skill_md(name, _settings().home)
        if found is None:
            raise HTTPException(status_code=404, detail="no such installed bundle")
        text, truncated = found
        return {"name": name, "text": text, "truncated": truncated}

    @app.post("/api/skills/bundles/{name}/status", dependencies=[guard], response_model=BundleOut)
    def set_bundle_status(name: str, body: BundleStatusIn) -> dict[str, Any]:
        """Switch an installed bundle on or off, keeping it on disk either way."""
        from chimera.skills.bundles import BundleError, set_status
        from chimera.skills.bundles import installed as installed_bundles

        try:
            found = set_status(name, _settings().home, body.status)
        except BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail="no such installed bundle")
        current = {b.name: b for b in installed_bundles(_settings().home)}
        return BundleOut(**current[name].to_dict()).model_dump()

    @app.delete("/api/skills/bundles/{name}", dependencies=[guard], response_model=RetiredOut)
    def delete_bundle(name: str) -> dict[str, Any]:
        """Delete an installed bundle and its files."""
        from chimera.skills.bundles import remove

        if not remove(name, _settings().home):
            raise HTTPException(status_code=404, detail="no such installed bundle")
        return {"retired": True}

    # ---- The curated library ----------------------------------------------------------------------
    # The routes above are about skills the agent LEARNED — an empty store on a fresh install, which
    # is what the Skills screen showed everyone on day one. The twenty-three cards that ship in the
    # box had no route at all, so the app could not mention them and the only documented way to use
    # one was a CLI command naming a repo-relative path.
    @app.get("/api/skills/library", dependencies=[guard], response_model=list[LibraryCardOut])
    def list_library() -> list[dict[str, Any]]:
        """The curated cards, metadata only — enough to browse, not enough to read."""
        from chimera.skills.library import load_library

        owned = set(_skill_store(_settings()).names())
        return [_library_card_dict(card, owned=owned) for card in load_library()]

    @app.get("/api/skills/library/{name}", dependencies=[guard], response_model=LibraryCardOut)
    def get_library_card(name: str) -> dict[str, Any]:
        """One card with its body — the Trigger/Do/Avoid/Check/Risk a person actually reads."""
        from chimera.skills.library import load_card

        card = load_card(name)
        if card is None:
            raise HTTPException(status_code=404, detail="no such curated skill card")
        owned = set(_skill_store(_settings()).names())
        return _library_card_dict(card, owned=owned, body=card.instructions)

    @app.post(
        "/api/skills/library/{name}/import", dependencies=[guard], response_model=LibraryImportOut
    )
    def import_library_card(name: str) -> dict[str, Any]:
        """Load a curated card into the user's store — `chimera skills-import <name>`, over HTTP.

        Runs the same validator the CLI does. The cards are ours and pass it, which is exactly why
        skipping it here would be the wrong economy: the gate is what makes the import path safe for
        the day a card arrives from somewhere else, and a second entrance that bypasses it is how a
        gate stops being one.
        """
        from chimera.governance import SkillValidator
        from chimera.skills.library import load_card
        from chimera.skills.skill_md import to_learned

        card = load_card(name)
        if card is None:
            raise HTTPException(status_code=404, detail="no such curated skill card")
        skill = to_learned(card)
        verdict = SkillValidator().validate(skill.to_dict())
        if not verdict.accepted:
            raise HTTPException(status_code=400, detail="; ".join(verdict.reasons))
        _skill_store(_settings()).add(skill)
        return {"imported": True, "name": skill.name, "status": skill.status}

    # ---- Cron -------------------------------------------------------------------------------------
    @app.get("/api/cron", dependencies=[guard], response_model=list[CronJobOut])
    def list_cron() -> list[dict[str, Any]]:
        return [_job_dict(j) for j in _cron_store(_settings()).list()]

    @app.get("/api/cron/results", dependencies=[guard], response_model=list[CronResultOut])
    def cron_results(job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """What the scheduled jobs answered, most recent first.

        Every dispatch has appended a line to `cron_results.jsonl` since the daemon existed, and
        nothing read it: the screen that creates a schedule promised to "save each result" and there
        was no way to see one without opening the file by hand.

        Declared BEFORE `/api/cron/{job_id}` matters — FastAPI matches in declaration order, and the
        parameterised route would otherwise swallow `results` as a job id and answer 404 for a path
        that exists.
        """
        from chimera.scheduler.results import load_results

        caminho = _settings().home / "scheduler" / "cron_results.jsonl"
        return [
            {
                "at": r.at,
                "job_id": r.job_id,
                "name": r.name,
                "action": r.action,
                "answer": r.answer,
                "delivered": r.delivered,
                "delivery_detail": r.delivery_detail,
                "skipped": r.skipped,
            }
            for r in load_results(caminho, job_id=job_id, limit=max(1, min(200, limit)))
        ]

    @app.get("/api/cron/silence", dependencies=[guard], response_model=CronSilenceOut)
    def cron_silence(grace_minutes: float = 5.0) -> dict[str, Any]:
        """Ask the schedule what it is not telling you.

        Every other honesty mechanism in this project sits downstream of a run having happened —
        the verifier judges a result, the receipt names who approved it. None of them gets a turn
        when the run never occurred, and a schedule that produced no result reads exactly like a
        schedule with nothing due. That distinction matters more now that the app shows results:
        an empty row for a job that never fired and one for a job that answered nothing look the
        same, and only one of them is a problem.

        On the desktop the daemon IS the app, so the common cause of an overdue job is that the
        window was closed when its time came. Nothing can watch while the process is down — a
        crashed process cannot log its own crash — so this is a question, not a watcher, and it is
        answered the moment anything asks.

        ``daemon`` is the third answer, and the one the jobs could not give: the daemon's own
        heartbeat (:mod:`chimera.scheduler.watchdog`), written every tick. A dead daemon with a
        daily job looks healthy from the jobs alone for ~23 hours — the job is not yet late — and
        the beat closes that window. ``unknown`` is the honest default when the beat carries no
        tick interval to judge freshness against; ``none`` is "no signal", not "dead".

        Declared BEFORE `/api/cron/{job_id}`: FastAPI matches in declaration order, and the
        parameterised route would otherwise take `silence` for a job id and 404 a path that exists.
        """
        import time

        from chimera.scheduler.engine import Scheduler
        from chimera.scheduler.watchdog import (
            default_heartbeat_path,
            infer_max_gap,
            watch_daemon,
            watch_tick_seconds,
        )

        grace = max(0.0, grace_minutes) * 60
        sched = Scheduler(_cron_store(_settings()))
        now = time.time()
        beat_path = default_heartbeat_path(_settings().home)
        # The ceiling is derived from the beat's own tick interval BEFORE the verdict — the
        # verdict is judged against it, not shown beside it. A beat without an interval yields
        # 0, which reads as "no number" (None): the reader refuses to judge freshness against
        # a ceiling the writer never left, so the verdict is `unknown`, not `stale`.
        intervalo = watch_tick_seconds(beat_path)
        teto = infer_max_gap(intervalo) if intervalo > 0 else None
        watch = watch_daemon(beat_path, now=now, max_gap_seconds=teto)
        return {
            "daemon": {
                "verdict": watch.verdict,
                "age_seconds": watch.age_seconds,
                "max_gap_seconds": teto,
            },
            "overdue": [
                {
                    "id": job.id,
                    "name": job.name,
                    "schedule": job.schedule,
                    "due_at": job.next_run or 0.0,
                    "behind_seconds": behind,
                }
                for job, behind in sched.overdue(now, grace=grace)
            ],
            "failing": [
                {
                    "id": job.id,
                    "name": job.name,
                    "consecutive_failures": job.consecutive_failures,
                    "last_status": job.last_status or "",
                    "last_error": job.last_error,
                }
                for job in sched.failing(at_least=1)
            ],
            "grace_seconds": grace,
        }

    # The weekly review's switch, for the Settings screen (`chimera/scheduler/weekly_review.py`).
    # It existed only as `chimera report weekly` (propose) plus `chimera cron enable` (switch on),
    # so a person who never opens a terminal could not have it. On proposes the job if it is not
    # there yet and enables it; off disables it and never creates one. The destination stays the
    # CLI's (`--deliver-to`): it is a credential, and only its host is shown here.
    def _weekly_review_dict(job: Any) -> dict[str, Any]:
        from chimera.scheduler.delivery import webhook_host_only

        if job is None:
            return {"proposed": False}
        return {
            "proposed": True,
            "job_id": job.id,
            "enabled": bool(job.enabled),
            "posts_to": webhook_host_only(job.deliver_to) if job.deliver_to else "",
        }

    @app.get("/api/cron/weekly-review", dependencies=[guard], response_model=WeeklyReviewOut)
    def get_weekly_review() -> dict[str, Any]:
        from chimera.scheduler.weekly_review import find_proposal

        return _weekly_review_dict(find_proposal(_cron_store(_settings()).list()))

    @app.put("/api/cron/weekly-review", dependencies=[guard], response_model=WeeklyReviewOut)
    def put_weekly_review(body: WeeklyReviewIn) -> dict[str, Any]:
        from chimera.scheduler import Scheduler
        from chimera.scheduler.weekly_review import find_proposal, propose

        sched = Scheduler(_cron_store(_settings()))
        job = find_proposal(sched.store.list())
        if job is None and not body.enabled:
            return _weekly_review_dict(None)
        if job is None:
            # The owner switched it on from the screen: a human-created job, not an agent proposal.
            job, _created = propose(sched, now=time.time(), created_by="human")
        if not body.enabled:
            return _weekly_review_dict(sched.disable(job.id))
        if job.created_by != "human":
            # An agent proposal the owner just adopted from the screen becomes theirs — the same
            # record the job would carry had they created it, so "who scheduled this" stays true.
            job.created_by = "human"
            sched.store.add(job)
        return _weekly_review_dict(sched.enable(job.id, now=time.time()))

    @app.post("/api/cron", dependencies=[guard], response_model=CronJobOut)
    def create_cron(body: CronCreateIn) -> dict[str, Any]:
        """Schedule a job from the UI — the CLI's `chimera cron add`, over HTTP. A human-created job
        is enabled immediately (unlike an agent-proposed one, which the scheduler starts disabled)."""
        from chimera.scheduler import Scheduler

        try:
            job = Scheduler(_cron_store(_settings())).schedule_cron(
                body.name,
                body.schedule,
                body.action,
                now=time.time(),
                created_by="human",
                workspace=body.workspace,
                deliver_to=body.deliver_to,
                verify=body.verify,
                max_attempts=body.max_attempts,
                notify=body.notify,
                tools=body.tools,
            )
        except ValueError as exc:  # an invalid cron expression is a client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _job_dict(job)

    @app.post("/api/cron/{job_id}/enable", dependencies=[guard], response_model=CronJobOut)
    def enable_cron(job_id: str) -> dict[str, Any]:
        from chimera.scheduler import Scheduler

        store = _cron_store(_settings())
        if job_id not in store:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_dict(Scheduler(store).enable(job_id, now=time.time()))

    @app.post("/api/cron/{job_id}/disable", dependencies=[guard], response_model=CronJobOut)
    def disable_cron(job_id: str) -> dict[str, Any]:
        from chimera.scheduler import Scheduler

        store = _cron_store(_settings())
        if job_id not in store:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_dict(Scheduler(store).disable(job_id))

    @app.delete("/api/cron/{job_id}", dependencies=[guard], response_model=DeletedOut)
    def delete_cron(job_id: str) -> dict[str, bool]:
        store = _cron_store(_settings())
        existed = job_id in store
        store.remove(job_id)
        return {"deleted": existed}

    # ---- Tasks: standalone kanban board + projects (with HITL approvals) --------------------------
    @app.get("/api/kanban", dependencies=[guard], response_model=dict[str, list[TaskCardOut]])
    def get_kanban() -> dict[str, Any]:
        from chimera.kanban import KanbanBoard
        from chimera.kanban.models import COLUMNS

        board = KanbanBoard(_settings().home / "kanban.json")
        return {col: [_card_dict(c) for c in board.cards(col)] for col in COLUMNS}

    @app.post("/api/kanban/cards", dependencies=[guard], response_model=TaskCardOut)
    def add_kanban_card(card: KanbanCardIn) -> dict[str, Any]:
        from chimera.kanban import KanbanBoard

        board = KanbanBoard(_settings().home / "kanban.json")
        # Action falls back to the title, matching the CLI: a one-line card should not have to say
        # the same sentence twice to be worth filing.
        created = board.add(
            card.title, card.action or card.title, lane=card.lane, verify=card.verify
        )
        return _card_dict(created)

    @app.patch("/api/kanban/cards/{card_id}", dependencies=[guard], response_model=TaskCardOut)
    def move_kanban_card(card_id: str, move: KanbanMoveIn) -> dict[str, Any]:
        from chimera.kanban import KanbanBoard
        from chimera.kanban.models import COLUMNS

        if move.column not in COLUMNS:
            raise HTTPException(status_code=400, detail=f"unknown column {move.column!r}")
        board = KanbanBoard(_settings().home / "kanban.json")
        if board.get(card_id) is None:
            raise HTTPException(status_code=404, detail="card not found")
        return _card_dict(board.move(card_id, move.column))  # type: ignore[arg-type]

    @app.delete("/api/kanban/cards/{card_id}", dependencies=[guard], response_model=DeletedOut)
    def remove_kanban_card(card_id: str) -> dict[str, bool]:
        from chimera.kanban import KanbanBoard

        board = KanbanBoard(_settings().home / "kanban.json")
        return {"deleted": board.remove(card_id)}

    @app.post("/api/kanban/run", dependencies=[guard], responses=SSE_RESPONSE)
    async def run_kanban(req: KanbanRunIn) -> EventSourceResponse:
        """Dispatch backlog cards through their lanes, streamed as each one finishes.

        The dispatch itself is unchanged and runs on a worker thread: it is a blocking loop that
        calls models, and running it on the event loop would freeze every other request for the
        length of the board.

        A card whose lane has no runner is deliberately left in the backlog rather than failed —
        that is what makes deleting an agent recoverable, and it is why the terminal frame reports
        how many were actually worked rather than how many were queued.
        """
        from chimera.core.registry import load as load_agents
        from chimera.kanban import KanbanBoard, LaneRunner, dispatch
        from chimera.kanban.lanes import CrewLane, SolveLane, runners_for

        settings = _settings()
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else default_workspace
        board = KanbanBoard(settings.home / "kanban.json")
        # Registered agents first, so an agent named after a built-in cannot take over the cards
        # already filed under it — the same ordering the CLI uses, for the same reason.
        runners: dict[str, LaneRunner] = {
            **runners_for(load_agents(settings.home), workspace=ws, model=req.model),
            "solve": SolveLane(workspace=ws, model=req.model),
            "crew": CrewLane(model=req.model),
        }

        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit_conflict(paths: list[str]) -> None:
            # A file two successful cards both changed. Only one version can come back, so the run
            # says which files that happened to instead of picking one and reporting two successes.
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {"event": "conflict", "data": json.dumps({"paths": paths})},
            )

        def emit(outcome: Any) -> None:
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "event": "card",
                    "data": json.dumps(
                        {
                            "card_id": outcome.card_id,
                            "lane": outcome.lane,
                            "success": outcome.success,
                            "moved_to": outcome.moved_to,
                        }
                    ),
                },
            )

        def work() -> None:
            done: dict[str, Any]
            try:
                outcomes = dispatch(
                    board,
                    runners,
                    limit=req.limit,
                    on_outcome=emit,
                    workers=req.workers,
                    workspace=ws,
                    on_conflict=emit_conflict,
                )
                done = {"worked": len(outcomes)}
            except Exception as exc:  # noqa: BLE001 — a dispatch failure is a frame, not a 500
                done = {"worked": 0, "error": str(exc)}
            loop.call_soon_threadsafe(
                queue.put_nowait, {"event": "done", "data": json.dumps(done)}
            )
            loop.call_soon_threadsafe(queue.put_nowait, None)

        async def stream() -> AsyncIterator[dict[str, Any]]:
            threading.Thread(target=work, daemon=True).start()
            while True:
                frame = await queue.get()
                if frame is None:
                    return
                yield frame

        return EventSourceResponse(stream())

    @app.post("/api/projects/draft", dependencies=[guard], response_model=SpecDraftOut)
    async def draft_project_spec(req: SpecDraftIn) -> dict[str, Any]:
        """Draft a spec from a plain-language description. One model call; nothing is written.

        A token-spending path, which this module otherwise avoids — noted here rather than hidden,
        the same way ``POST /api/kanban/run`` is. It earns the exception because the orchestrator
        it feeds is the most capable thing in the app and its only door was a field asking for the
        path of a YAML file: everyone who cannot write that YAML was standing outside it.

        Drafting and writing are separate calls so the requirements that reach disk are the ones
        the person kept. Reviewing them is not a formality — the spec is the acceptance authority,
        so a requirement nobody understood is a project that finishes on a condition nobody chose.
        """

        def work() -> dict[str, Any]:
            from chimera.orchestration.draft import DraftError, draft_spec
            from chimera.providers import LLMGateway

            try:
                drafted = draft_spec(req.description, LLMGateway())
            except DraftError as exc:
                # An honest empty draft with the reason, never a 500: the description was the
                # user's, and "I could not turn that into a spec" is a sentence they can act on.
                return {"name": "", "requirements": [], "note": str(exc)}
            except Exception as exc:  # noqa: BLE001 — a model hiccup is not a server error
                _log.warning("spec draft failed: %s", exc)
                return {"name": "", "requirements": [], "note": "the draft call did not complete"}
            return {
                "name": drafted.spec.name,
                "requirements": [r.model_dump() for r in drafted.spec.requirements],
                "refused_commands": drafted.refused_commands,
                "refused_ids": list(drafted.refused_ids),
                "note": "",
            }

        return await asyncio.get_running_loop().run_in_executor(None, work)

    @app.post("/api/projects/spec", dependencies=[guard], response_model=SpecWriteOut)
    def write_project_spec(req: SpecWriteIn) -> dict[str, Any]:
        """Write a reviewed spec into the project folder. No model call.

        ``command`` is refused here too, and not only in the drafter. The rule has to hold at the
        boundary that creates the file, or it is bypassable by anyone who edits the JSON on the way
        past — and a bug in the screen could write a shell command into the thing that judges the
        project. Somebody who wants a ``command`` check writes the YAML themselves, which is a
        different act: they chose the command.
        """
        from chimera.governance.drift import Requirement, Spec
        from chimera.orchestration.draft import DraftError, write_spec

        if any(r.check == "command" for r in req.requirements):
            raise HTTPException(
                status_code=400,
                detail="a 'command' check runs a shell command; write that spec file yourself",
            )
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else default_workspace
        try:
            spec = Spec(
                name=req.name,
                requirements=[Requirement.model_validate(r.model_dump()) for r in req.requirements],
            )
            path = write_spec(spec, ws)
        except (DraftError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"path": str(path)}

    @app.post("/api/projects", dependencies=[guard], response_model=ProjectStateOut)
    def start_project(req: ProjectStartIn) -> dict[str, Any]:
        """Create a project. Deliberately does NOT advance it.

        The CLI's `project start` creates and runs in one command, which is right for a terminal
        where the output scrolls past you. Here they are separate calls so the screen can show what
        was created — its id, its plan-approval pause — before anything spends a token. Advancing is
        `POST /api/projects/{id}/step`.
        """
        from chimera.kanban.lanes import SolveLane
        from chimera.orchestration.project import ProjectConfig, ProjectOrchestrator

        spec = Path(req.spec).expanduser()
        if not spec.is_file():
            raise HTTPException(status_code=400, detail=f"spec not found: {req.spec}")
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else default_workspace
        try:
            proj = ProjectOrchestrator.start(
                spec,
                ws,
                home=_settings().home,
                # Constructing the lane makes NO model call — the LLM runs inside step()/run(), and
                # this endpoint calls neither.
                solve_card=SolveLane(workspace=ws),
                config=ProjectConfig(
                    max_iterations=req.max_iterations,
                    require_plan_approval=not req.auto_approve,
                ),
            )
        except ValueError as exc:
            # A spec with no required requirements would report "done" having verified nothing.
            # Refusing it is the orchestrator's rule; surfacing the reason is this endpoint's job.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _state_dict(proj.state)

    @app.post("/api/projects/{project_id}/step", dependencies=[guard],
              response_model=ProjectStateOut)
    async def step_project(project_id: str) -> dict[str, Any]:
        """Advance one iteration: check the spec, sync cards, work at most ONE ready card.

        One step per call, and no server-side `run` loop, which is a decision rather than an
        omission. `run()` is repeated `step()`, so a client that has this can loop it — and a
        client-side loop can be stopped between iterations and shows the state after each one. A
        server-side run would be neither interruptible nor observable until it ended.

        Runs on a worker thread: a step calls models, and holding the event loop for it would freeze
        every other request for as long as a card takes.
        """
        proj = _load_project(project_id, _settings())
        return _state_dict(await run_in_threadpool(proj.step))

    @app.get("/api/projects", dependencies=[guard], response_model=list[ProjectStateOut])
    def list_projects() -> list[dict[str, Any]]:
        from chimera.orchestration.project import ProjectState

        root = _settings().home / "projects"
        out: list[dict[str, Any]] = []
        if root.exists():
            for state_path in sorted(root.glob("*/project.json")):
                try:
                    out.append(_state_dict(ProjectState.load(state_path)))
                except Exception as exc:  # noqa: BLE001 — a corrupt project must not break the list
                    _log.debug("skipping unreadable project %s: %s", state_path, exc)
        return out

    @app.get("/api/projects/{project_id}", dependencies=[guard], response_model=ProjectDetailOut)
    def get_project(project_id: str) -> dict[str, Any]:
        from chimera.kanban import KanbanBoard
        from chimera.kanban.models import COLUMNS
        from chimera.orchestration.project import ProjectOrchestrator, ProjectState

        home = _settings().home
        state_path = ProjectOrchestrator.project_dir(home, project_id) / "project.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="project not found")
        state = ProjectState.load(state_path)
        board = KanbanBoard(Path(state.board_path))
        columns = {col: [_card_dict(c) for c in board.cards(col)] for col in COLUMNS}
        return {"state": _state_dict(state), "columns": columns}

    @app.post("/api/projects/{project_id}/approve", dependencies=[guard], response_model=ProjectStateOut)
    def approve_project(project_id: str, body: ApproveBody) -> dict[str, Any]:
        orch = _load_project(project_id, _settings())
        state = orch.approve_card(body.card) if body.card else orch.approve_plan()
        return _state_dict(state)

    @app.post("/api/projects/{project_id}/deny", dependencies=[guard], response_model=ProjectStateOut)
    def deny_project(project_id: str, body: ApproveBody) -> dict[str, Any]:
        if not body.card:
            raise HTTPException(status_code=400, detail="card id required to deny")
        return _state_dict(_load_project(project_id, _settings()).deny_card(body.card))
