"""Feature endpoints for the desktop app — Memory, Skills, Cron, Tasks (M21 Fase C).

Each endpoint reuses an existing manager/store (the same one the matching ``chimera`` CLI command
builds), so the UI is a view over the real state, never a reimplementation. Reads and the HITL
approve/deny writes are pure file I/O — no live LLM call. The token-spending paths (running a project
step, executing a skill, consolidating memory) are deliberately NOT exposed here; the app drives those
through the streaming chat / solve flows instead.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params
from pydantic import BaseModel

from chimera.api.schemas import (
    ApprovedOut,
    CronJobOut,
    DeletedOut,
    MemoryAddOut,
    MemoryItemOut,
    MemoryProfileOut,
    ProjectDetailOut,
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


def register_features(app: FastAPI, guard: params.Depends) -> None:
    """Attach the Fase C feature routes to ``app``. ``guard`` enforces the bearer token on mutations."""

    # ---- Memory -----------------------------------------------------------------------------------
    @app.get("/api/memory", response_model=list[MemoryItemOut])
    def list_memory(q: str = "", k: int = 30) -> list[dict[str, Any]]:
        mgr = _memory_manager()
        items = mgr.search(q, k=k) if q.strip() else mgr.store.all()
        return [_item_dict(it) for it in items]

    @app.get("/api/memory/profile", response_model=MemoryProfileOut)
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
    @app.get("/api/skills", response_model=SkillsOut)
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
    @app.get("/api/cron", response_model=list[CronJobOut])
    def list_cron() -> list[dict[str, Any]]:
        return [_job_dict(j) for j in _cron_store().list()]

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
    @app.get("/api/kanban", response_model=dict[str, list[TaskCardOut]])
    def get_kanban() -> dict[str, Any]:
        from chimera.kanban import KanbanBoard
        from chimera.kanban.models import COLUMNS

        board = KanbanBoard(get_settings().home / "kanban.json")
        return {col: [_card_dict(c) for c in board.cards(col)] for col in COLUMNS}

    @app.get("/api/projects", response_model=list[ProjectStateOut])
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

    @app.get("/api/projects/{project_id}", response_model=ProjectDetailOut)
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
