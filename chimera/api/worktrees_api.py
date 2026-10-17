"""``GET /api/code/projects/worktrees`` and ``POST …/prune``: the Code screen's worktree cleanup.

Every decision is in `chimera/core/worktree_prune.py`. What this layer adds is what only the app
knows: which conversations point at a worktree, and whether one of them is busy — a turn running, a
message queued, a background work unfinished, a question waiting. A busy worktree is kept with
``in_use``, exactly like one a run is working in.

The removal is the owner's act. The bridge lists the worktrees at its operate tier and never reaches
the prune at any tier (`bridge_routes.OWNER_DECISION_ROUTES`). Conversations of a removed worktree
are ARCHIVED, never deleted: the transcript is the record of the work, and the folder was only where
it happened.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from chimera.core.worktree_prune import WorktreeRow, list_worktrees, prune_worktrees

#: The untracked files a row carries; the count says how many there were in all.
_UNTRACKED_SHOWN = 20


class ProjectWorktreeConversationOut(BaseModel):
    id: str
    title: str
    archived: bool


class ProjectWorktreeOut(BaseModel):
    """One worktree. ``removable`` is False whenever ``kept`` names a reason (a stable string)."""

    path: str
    branch: str
    main: bool
    exists: bool
    locked: bool
    owned: bool
    in_use: bool
    merged: bool
    clean: bool
    untracked: list[str]
    untracked_count: int
    upstream: str
    ahead: int | None
    removable: bool
    kept: str
    conversations: list[ProjectWorktreeConversationOut]


class ProjectWorktreesOut(BaseModel):
    project: str
    main: str
    worktrees: list[ProjectWorktreeOut]


class ProjectWorktreePruneIn(BaseModel):
    """The worktrees the owner picked. ``dry_run`` defaults to True: a preview unless told."""

    project: str = Field(min_length=1, max_length=4096)
    paths: list[str] = Field(min_length=1, max_length=500)
    dry_run: bool = True


class ProjectWorktreePruneResultOut(BaseModel):
    path: str
    branch: str
    kept: str
    removed: bool
    would_remove: bool
    branch_deleted: bool
    archived: list[str]


class ProjectWorktreePruneOut(BaseModel):
    dry_run: bool
    results: list[ProjectWorktreePruneResultOut]


def _key(path: Path | str) -> str:
    try:
        return os.path.normcase(str(Path(path).expanduser().resolve()))
    except OSError:
        return os.path.normcase(str(Path(path).expanduser().absolute()))


def register_worktree_api(
    app: FastAPI,
    guard: params.Depends,
    *,
    store: Any,
    live_turns: Any,
    work_store: Any,
    durable_queue: Any,
    waiting_sessions: Callable[[], set[str]],
) -> None:
    """Mount the two routes on ``app``. The stores are the Code API's own objects."""

    def conversations_by_folder() -> dict[str, list[dict[str, Any]]]:
        by_folder: dict[str, list[dict[str, Any]]] = {}
        marks = store.marks.all()
        for meta in store.list_meta():
            folder = str(meta.get("workspace") or "")
            if not folder:
                continue
            sid = str(meta["id"])
            mark = marks.get(sid)
            by_folder.setdefault(_key(folder), []).append(
                {
                    "id": sid,
                    "title": str(meta.get("title") or ""),
                    "archived": mark is not None and mark.archived_at is not None,
                }
            )
        return by_folder

    def busy_folders(by_folder: dict[str, list[dict[str, Any]]]) -> set[str]:
        """The folders something is happening in right now."""
        busy = {_key(t.workspace) for t in live_turns.running() if t.workspace}
        active = work_store.active_parents()
        for parent in active:
            busy.update(_key(w.workspace) for w in work_store.for_parent(parent) if w.active)
        held = waiting_sessions() | active | set(durable_queue.sessions_with_pending())
        for folder, rows in by_folder.items():
            if any(row["id"] in held for row in rows):
                busy.add(folder)
        return busy

    def row_out(row: WorktreeRow, by_folder: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return {
            "path": row.path,
            "branch": row.branch,
            "main": row.main,
            "exists": row.exists,
            "locked": row.locked,
            "owned": row.owned,
            "in_use": row.in_use,
            "merged": row.merged,
            "clean": row.clean,
            "untracked": list(row.untracked[:_UNTRACKED_SHOWN]),
            "untracked_count": len(row.untracked),
            "upstream": row.upstream,
            "ahead": row.ahead,
            "removable": row.removable,
            "kept": row.kept,
            "conversations": by_folder.get(_key(row.path), []),
        }

    def project_folder(project: str) -> Path:
        folder = Path(project).expanduser()
        if not folder.is_absolute() or not folder.is_dir():
            raise HTTPException(status_code=400, detail="project must be an existing absolute folder")
        return folder

    @app.get(
        "/api/code/projects/worktrees", dependencies=[guard], response_model=ProjectWorktreesOut
    )
    async def list_project_worktrees(project: str) -> dict[str, Any]:
        """Every worktree of the project's repository, each with the reason it is kept or
        ``removable``. Reads only: git's own listing, ``status`` and refs."""
        folder = project_folder(project)
        by_folder = conversations_by_folder()
        busy = busy_folders(by_folder)
        found = await run_in_threadpool(
            list_worktrees, folder, busy=lambda p: _key(p) in busy
        )
        if found is None:
            raise HTTPException(status_code=400, detail="project is not in a git repository")
        main, rows = found
        return {
            "project": str(folder),
            "main": str(main),
            "worktrees": [row_out(row, by_folder) for row in rows],
        }

    @app.post(
        "/api/code/projects/worktrees/prune", dependencies=[guard], response_model=ProjectWorktreePruneOut
    )
    async def prune_project_worktrees(body: ProjectWorktreePruneIn) -> dict[str, Any]:
        """Remove the picked worktrees every refusal allows; with ``dry_run`` (the default) only say
        which would go. A kept one is a 200 with ``kept`` naming why. Removal is ``git worktree
        remove`` without ``--force``, then ``git branch -d``; each removed worktree's conversations
        are archived, never deleted."""
        folder = project_folder(body.project)
        by_folder = conversations_by_folder()
        busy = busy_folders(by_folder)
        results = await run_in_threadpool(
            lambda: prune_worktrees(
                folder, body.paths, dry_run=body.dry_run, busy=lambda p: _key(p) in busy
            )
        )
        if results is None:
            raise HTTPException(status_code=400, detail="project is not in a git repository")
        out: list[dict[str, Any]] = []
        now = time.time()
        for result in results:
            archived: list[str] = []
            if result.removed:
                for conv in by_folder.get(_key(result.path), []):
                    if not conv["archived"]:
                        store.marks.archive(conv["id"], at=now)
                        archived.append(conv["id"])
            out.append(
                {
                    "path": result.path,
                    "branch": result.branch,
                    "kept": result.kept,
                    "removed": result.removed,
                    "would_remove": result.would_remove,
                    "branch_deleted": result.branch_deleted,
                    "archived": archived,
                }
            )
        return {"dry_run": body.dry_run, "results": out}
