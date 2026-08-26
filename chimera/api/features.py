"""Feature endpoints for the desktop app — Memory, Skills, Cron, Tasks (M21 Fase C).

Each endpoint reuses an existing manager/store (the same one the matching ``chimera`` CLI command
builds), so the UI is a view over the real state, never a reimplementation. Reads and the HITL
approve/deny writes are pure file I/O — no live LLM call. The token-spending paths (running a project
step, executing a skill, consolidating memory) are deliberately NOT exposed here; the app drives those
through the streaming chat / solve flows instead.

The one exception is ``POST /api/kanban/run``, and it is streamed for the same reason those are: a
board dispatch calls models for as long as it has cards, so it reports each card as it lands rather
than returning once at the end. Without it the board was a display case — every route it had was a
read, so the screen could show the work and change nothing about it.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from starlette.concurrency import run_in_threadpool

from chimera.api.schemas import (
    ApprovedOut,
    CronCreateIn,
    CronJobOut,
    DeletedOut,
    KanbanCardIn,
    KanbanMoveIn,
    KanbanRunIn,
    LibraryCardOut,
    LibraryImportOut,
    MemoryAddOut,
    MemoryItemOut,
    MemoryLayersOut,
    MemoryProfileOut,
    ProjectDetailOut,
    ProjectStartIn,
    ProjectStateOut,
    RetiredOut,
    SkillsOut,
    TaskCardOut,
)
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
        "created_by": job.created_by,
        "workspace": job.workspace,
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
def _cron_store() -> Any:
    from chimera.scheduler import CronStore

    return CronStore(get_settings().home / "scheduler" / "jobs.json")


def _memory_manager() -> Any:
    from chimera.evolution.wiring import build_memory_manager

    return build_memory_manager(get_settings())


def _skill_store() -> Any:
    from chimera.evolution import SkillStore

    return SkillStore(get_settings().home / "skills.json")


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


def _load_project(project_id: str) -> Any:
    """Rebuild the orchestrator from disk for an HITL write. Constructing the solve lane makes NO
    model call — the LLM only runs inside step()/run(), which the approve/deny endpoints never call.
    """
    from chimera.kanban.lanes import SolveLane
    from chimera.orchestration.project import ProjectOrchestrator, ProjectState

    home = get_settings().home
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


class BundleStatusIn(BaseModel):
    status: str = Field(default="active", description="active | inactive")


def register_features(
    app: FastAPI, guard: params.Depends, *, workspace: Path | None = None
) -> None:
    """Attach the Fase C feature routes to ``app``. ``guard`` enforces the bearer token on mutations.

    ``workspace`` is where a dispatched card works when the request names none. Optional so every
    existing caller keeps working; absent, it falls back to the process directory, which is what the
    board did before it could be dispatched from here at all.
    """
    default_workspace = workspace or Path.cwd()

    # ---- Memory -----------------------------------------------------------------------------------
    @app.get("/api/memory", dependencies=[guard], response_model=list[MemoryItemOut])
    def list_memory(q: str = "", k: int = 30) -> list[dict[str, Any]]:
        k = max(1, min(k, 200))  # clamp: a negative/huge k must not dump the whole store
        mgr = _memory_manager()
        items = mgr.search(q, k=k) if q.strip() else mgr.store.all()
        return [_item_dict(it) for it in items]

    @app.get("/api/memory/layers", dependencies=[guard], response_model=MemoryLayersOut)
    def memory_layers() -> dict[str, Any]:
        from chimera.api.memory_layers import summarize_memory_layers

        settings = get_settings()
        return summarize_memory_layers(
            _memory_manager().store.all(),
            semantic_embeddings_enabled=settings.semantic_memory,
        )

    @app.get("/api/memory/profile", dependencies=[guard], response_model=MemoryProfileOut)
    def memory_profile() -> dict[str, Any]:
        mgr = _memory_manager()
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
        mgr = _memory_manager()
        # `project` omitted means everywhere, and that is right for this route: a fact typed
        # by hand into the Memory screen is the owner stating something, not the agent noting
        # what it learned while working in a folder.
        status, item = mgr.remember(
            body.content, body.kind, key=body.key, project=body.project
        )
        return {"status": status, "item": _item_dict(item)}

    @app.delete("/api/memory/{item_id}", dependencies=[guard], response_model=DeletedOut)
    def delete_memory(item_id: str) -> dict[str, bool]:
        _memory_manager().delete(item_id)
        return {"deleted": True}

    # ---- Skills -----------------------------------------------------------------------------------
    @app.get("/api/skills", dependencies=[guard], response_model=SkillsOut)
    def list_skills() -> dict[str, Any]:
        store = _skill_store()
        return {"stats": store.stats(), "retirement_candidates": store.retirement_candidates()}

    @app.post("/api/skills/{name}/approve", dependencies=[guard], response_model=ApprovedOut)
    def approve_skill(name: str) -> dict[str, bool]:
        if not _skill_store().approve(name):
            raise HTTPException(status_code=404, detail="skill not found")
        return {"approved": True}

    @app.post("/api/skills/{name}/retire", dependencies=[guard], response_model=RetiredOut)
    def retire_skill(name: str) -> dict[str, bool]:
        if not _skill_store().retire(name):
            raise HTTPException(status_code=404, detail="skill not found")
        return {"retired": True}

    # ---- Installable skills from the wider ecosystem -----------------------------------------
    # A different SHAPE of skill from everything above, not a longer list of the same one. A card
    # is text that goes in the prompt; these are directories that ship scripts and reference files,
    # so they live on disk and what reaches the prompt is a name and a path. See
    # `chimera/skills/bundles.py` for why the store could not hold them.
    def _bundle_dicts() -> dict[str, str]:
        from chimera.skills.bundles import installed as installed_bundles

        return {b.name: b.status for b in installed_bundles(get_settings().home)}

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

        return [BundleOut(**b.to_dict()).model_dump() for b in installed_bundles(get_settings().home)]

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
            record = install(entry, get_settings().home, force=force)
        except BundleError as exc:
            # The message is written to be read by a person: which limit, which file, which host.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return BundleOut(**record.to_dict()).model_dump()

    @app.post("/api/skills/bundles/{name}/status", dependencies=[guard], response_model=BundleOut)
    def set_bundle_status(name: str, body: BundleStatusIn) -> dict[str, Any]:
        """Switch an installed bundle on or off, keeping it on disk either way."""
        from chimera.skills.bundles import BundleError, set_status
        from chimera.skills.bundles import installed as installed_bundles

        try:
            found = set_status(name, get_settings().home, body.status)
        except BundleError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail="no such installed bundle")
        current = {b.name: b for b in installed_bundles(get_settings().home)}
        return BundleOut(**current[name].to_dict()).model_dump()

    @app.delete("/api/skills/bundles/{name}", dependencies=[guard], response_model=RetiredOut)
    def delete_bundle(name: str) -> dict[str, Any]:
        """Delete an installed bundle and its files."""
        from chimera.skills.bundles import remove

        if not remove(name, get_settings().home):
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

        owned = set(_skill_store().names())
        return [_library_card_dict(card, owned=owned) for card in load_library()]

    @app.get("/api/skills/library/{name}", dependencies=[guard], response_model=LibraryCardOut)
    def get_library_card(name: str) -> dict[str, Any]:
        """One card with its body — the Trigger/Do/Avoid/Check/Risk a person actually reads."""
        from chimera.skills.library import load_card

        card = load_card(name)
        if card is None:
            raise HTTPException(status_code=404, detail="no such curated skill card")
        owned = set(_skill_store().names())
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
        _skill_store().add(skill)
        return {"imported": True, "name": skill.name, "status": skill.status}

    # ---- Cron -------------------------------------------------------------------------------------
    @app.get("/api/cron", dependencies=[guard], response_model=list[CronJobOut])
    def list_cron() -> list[dict[str, Any]]:
        return [_job_dict(j) for j in _cron_store().list()]

    @app.post("/api/cron", dependencies=[guard], response_model=CronJobOut)
    def create_cron(body: CronCreateIn) -> dict[str, Any]:
        """Schedule a job from the UI — the CLI's `chimera cron add`, over HTTP. A human-created job
        is enabled immediately (unlike an agent-proposed one, which the scheduler starts disabled)."""
        from chimera.scheduler import Scheduler

        try:
            job = Scheduler(_cron_store()).schedule_cron(
                body.name,
                body.schedule,
                body.action,
                now=time.time(),
                created_by="human",
                workspace=body.workspace,
            )
        except ValueError as exc:  # an invalid cron expression is a client error, not a 500
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _job_dict(job)

    @app.post("/api/cron/{job_id}/enable", dependencies=[guard], response_model=CronJobOut)
    def enable_cron(job_id: str) -> dict[str, Any]:
        from chimera.scheduler import Scheduler

        store = _cron_store()
        if job_id not in store:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_dict(Scheduler(store).enable(job_id, now=time.time()))

    @app.post("/api/cron/{job_id}/disable", dependencies=[guard], response_model=CronJobOut)
    def disable_cron(job_id: str) -> dict[str, Any]:
        from chimera.scheduler import Scheduler

        store = _cron_store()
        if job_id not in store:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_dict(Scheduler(store).disable(job_id))

    @app.delete("/api/cron/{job_id}", dependencies=[guard], response_model=DeletedOut)
    def delete_cron(job_id: str) -> dict[str, bool]:
        store = _cron_store()
        existed = job_id in store
        store.remove(job_id)
        return {"deleted": existed}

    # ---- Tasks: standalone kanban board + projects (with HITL approvals) --------------------------
    @app.get("/api/kanban", dependencies=[guard], response_model=dict[str, list[TaskCardOut]])
    def get_kanban() -> dict[str, Any]:
        from chimera.kanban import KanbanBoard
        from chimera.kanban.models import COLUMNS

        board = KanbanBoard(get_settings().home / "kanban.json")
        return {col: [_card_dict(c) for c in board.cards(col)] for col in COLUMNS}

    @app.post("/api/kanban/cards", dependencies=[guard], response_model=TaskCardOut)
    def add_kanban_card(card: KanbanCardIn) -> dict[str, Any]:
        from chimera.kanban import KanbanBoard

        board = KanbanBoard(get_settings().home / "kanban.json")
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
        board = KanbanBoard(get_settings().home / "kanban.json")
        if board.get(card_id) is None:
            raise HTTPException(status_code=404, detail="card not found")
        return _card_dict(board.move(card_id, move.column))  # type: ignore[arg-type]

    @app.delete("/api/kanban/cards/{card_id}", dependencies=[guard], response_model=DeletedOut)
    def remove_kanban_card(card_id: str) -> dict[str, bool]:
        from chimera.kanban import KanbanBoard

        board = KanbanBoard(get_settings().home / "kanban.json")
        return {"deleted": board.remove(card_id)}

    @app.post("/api/kanban/run", dependencies=[guard])
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

        settings = get_settings()
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
                home=get_settings().home,
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
        proj = _load_project(project_id)
        return _state_dict(await run_in_threadpool(proj.step))

    @app.get("/api/projects", dependencies=[guard], response_model=list[ProjectStateOut])
    def list_projects() -> list[dict[str, Any]]:
        from chimera.orchestration.project import ProjectState

        root = get_settings().home / "projects"
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

        home = get_settings().home
        state_path = ProjectOrchestrator.project_dir(home, project_id) / "project.json"
        if not state_path.exists():
            raise HTTPException(status_code=404, detail="project not found")
        state = ProjectState.load(state_path)
        board = KanbanBoard(Path(state.board_path))
        columns = {col: [_card_dict(c) for c in board.cards(col)] for col in COLUMNS}
        return {"state": _state_dict(state), "columns": columns}

    @app.post("/api/projects/{project_id}/approve", dependencies=[guard], response_model=ProjectStateOut)
    def approve_project(project_id: str, body: ApproveBody) -> dict[str, Any]:
        orch = _load_project(project_id)
        state = orch.approve_card(body.card) if body.card else orch.approve_plan()
        return _state_dict(state)

    @app.post("/api/projects/{project_id}/deny", dependencies=[guard], response_model=ProjectStateOut)
    def deny_project(project_id: str, body: ApproveBody) -> dict[str, Any]:
        if not body.card:
            raise HTTPException(status_code=400, detail="card id required to deny")
        return _state_dict(_load_project(project_id).deny_card(body.card))
