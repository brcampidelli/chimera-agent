"""Data model for scheduled jobs (crons and event-triggered SOPs)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Trigger = Literal["cron", "event", "webhook"]
CreatedBy = Literal["human", "agent"]


class CronJob(BaseModel):
    """A scheduled job.

    ``trigger='cron'`` uses a cron expression in :attr:`schedule`; ``trigger='event'``
    uses an event name. ``created_by`` records whether a human assigned the job or the
    agent learned it (self-learned crons arrive in M4).
    """

    id: str
    name: str
    trigger: Trigger = "cron"
    schedule: str
    action: str
    created_by: CreatedBy = "human"
    enabled: bool = True
    next_run: float | None = None
    last_run: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
