"""Explicitly approved, durable one-shot chat schedules (opt-in only)."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from chimera.scheduler.engine import Scheduler
from chimera.scheduler.store import CronStore
from chimera.tools.base import Tool


class ScheduleOnceTool(Tool):
    name = "schedule_once"
    description = (
        "Schedule one future reminder/action. Requires an ISO-8601 datetime with an explicit "
        "UTC offset. The job is persisted for the scheduler daemon. Every request requires "
        "owner approval; approval denial means no schedule was created."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "run_at": {"type": "string", "description": "ISO-8601 datetime with UTC offset."},
            "task": {"type": "string", "description": "The action to run at that time."},
        },
        "required": ["run_at", "task"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        *,
        home: Path,
        workspace: Path,
        approve: Callable[[str, str], bool],
        now: Callable[[], float] | None = None,
    ) -> None:
        self._home = Path(home)
        self._workspace = Path(workspace)
        self._approve = approve
        self._now = now or __import__("time").time

    def run(self, **kwargs: Any) -> str:
        task = str(kwargs.get("task") or "").strip()
        if not task:
            return "error: task is required"
        try:
            when = datetime.fromisoformat(str(kwargs.get("run_at") or ""))
            if when.tzinfo is None:
                return "error: run_at must include a UTC offset"
            timestamp = when.timestamp()
        except (TypeError, ValueError, OverflowError):
            return "error: run_at must be a valid ISO-8601 datetime with UTC offset"
        now = self._now()
        if timestamp <= now:
            return "error: run_at must be in the future"
        action = f"At {when.isoformat()}, perform this reminder: {task}"
        if not self._approve(action, "Create a persistent one-shot reminder in the scheduler"):
            return "schedule not created: owner approval was not granted"
        scheduler = Scheduler(CronStore(self._home / "scheduler" / "jobs.json"))
        job = scheduler.schedule_once(
            task[:80], timestamp, action, now=now, workspace=str(self._workspace)
        )
        return f"schedule created: {job.id} for {when.isoformat()}"


__all__ = ["ScheduleOnceTool"]
