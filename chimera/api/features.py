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
from pydantic import BaseModel
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
    }


def _item_dict(item: Any) -> dict[str, Any]:
    return {
        "id": item.id,
        "content": item.content,
        "kind": item.kind,
        "provenance": item.provenance,
        "source": item.source,
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


class ApproveBody(BaseModel):
    card: str | None = None


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
            "profile": mgr.profile(),
            "persona": [_item_dict(it) for it in mgr.store.by_kind("persona")],
        }

    @app.post("/api/memory", dependencies=[guard], response_model=MemoryAddOut)
    def add_memory(body: MemoryAdd) -> dict[str, Any]:
        if body.kind not in _MEMORY_KINDS:
            raise HTTPException(status_code=400, detail=f"kind must be one of {sorted(_MEMORY_KINDS)}")
        mgr = _memory_manager()
        status, item = mgr.remember(body.content, body.kind, key=body.key)
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
                body.name, body.schedule, body.action, now=time.time(), created_by="human"
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
