"""Durable run state — checkpoint the solve loop by thread so a crash can resume.

``WorkspaceGuard`` snapshots *files* (for verify-or-revert within a run); this persists the
*run* itself. The autonomous loop saves its orchestration state (task, attempts so far, the
current feedback and plan) to SQLite after every attempt, keyed by a ``thread_id``. If the
process dies mid-run, re-running with the same thread resumes from the last checkpoint instead
of starting over. On normal completion the checkpoint is deleted — only an interrupted run
leaves one behind.

Honest scope: this resumes the *loop*, not the workspace. Files are the user's workspace,
assumed intact on resume (verify-or-revert already leaves them at the last good snapshot); we
never re-run an attempt that already succeeded, because success returns and clears the thread.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from chimera.telemetry import get_logger

_log = get_logger("core.runstate")


class RunCheckpointer:
    """A thread-keyed store of JSON run state, backed by SQLite."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS run_state (thread_id TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, thread_id: str, state: dict[str, Any]) -> None:
        """Upsert the run state for ``thread_id`` (overwrites the previous checkpoint)."""
        payload = json.dumps(state)
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO run_state (thread_id, state) VALUES (?, ?) "
                "ON CONFLICT(thread_id) DO UPDATE SET state = excluded.state",
                (thread_id, payload),
            )

    def load(self, thread_id: str) -> dict[str, Any] | None:
        """Return the saved state for ``thread_id``, or None if there is none."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT state FROM run_state WHERE thread_id = ?", (thread_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            loaded = json.loads(row[0])
        except json.JSONDecodeError:
            _log.warning("corrupt run-state for thread %s; ignoring", thread_id)
            return None
        return loaded if isinstance(loaded, dict) else None

    def delete(self, thread_id: str) -> None:
        """Drop the checkpoint for ``thread_id`` (called when the run completes or is denied)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM run_state WHERE thread_id = ?", (thread_id,))

    def approve(self, thread_id: str) -> bool:
        """Mark a paused (awaiting-approval) run approved so a resume finalizes it.

        Returns False if there's no checkpoint or it isn't awaiting approval.
        """
        state = self.load(thread_id)
        if state is None or not state.get("awaiting_approval"):
            return False
        state["approved"] = True
        state["awaiting_approval"] = False
        self.save(thread_id, state)
        return True

    def threads(self) -> list[str]:
        """All thread ids with a live checkpoint (resumable runs)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT thread_id FROM run_state ORDER BY thread_id").fetchall()
        return [row[0] for row in rows]

    def fork(self, src_thread: str, dst_thread: str, *, overwrite: bool = False) -> bool:
        """Branch a checkpoint: copy ``src_thread``'s state onto a new ``dst_thread`` (LangGraph fork).

        The point of a fork is a *paired experiment*: replay two policies (baseline vs candidate)
        from the identical captured state, so the only variable is the policy and the A/B is paired
        (see :mod:`chimera.eval.paired`). Returns False if the source is missing, or the destination
        already exists and ``overwrite`` is False.
        """
        state = self.load(src_thread)
        if state is None:
            return False
        if not overwrite and self.load(dst_thread) is not None:
            _log.debug("fork target %s already exists; refusing (overwrite=False)", dst_thread)
            return False
        self.save(dst_thread, state)
        _log.debug("forked checkpoint %s -> %s", src_thread, dst_thread)
        return True
