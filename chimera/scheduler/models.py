"""Data model for scheduled jobs (crons and event-triggered SOPs)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Trigger = Literal["cron", "event", "webhook"]
CreatedBy = Literal["human", "agent"]

DispatchStatus = Literal["ok", "error", "timeout"]
"""What happened on the last dispatch — which is not the same question as whether one happened.

``last_run`` records the ATTEMPT: the scheduler sets it outside the try/except on purpose, so a job
that raises or overruns still has its schedule advanced and the tick moves on. That is right for
the scheduler and misleading for anyone reading the field to find out whether the job is working —
a job that has failed on every tick for a month has a `last_run` of a minute ago.

So the outcome is recorded beside it rather than folded into it. Two silences look identical from
`last_run` alone and need completely different responses: *nothing ran* means look at the daemon,
*everything ran and failed* means look at the job.
"""


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
    """When a dispatch was last ATTEMPTED. Says nothing about whether it worked — see
    :data:`DispatchStatus`."""
    last_status: DispatchStatus | None = None
    """How that attempt ended. ``None`` on a job that has never been dispatched."""
    last_error: str | None = None
    """The failure, in one line, for the two statuses that have one."""
    consecutive_failures: int = 0
    """Failures since the last success. One failure is weather; forty is a broken job, and the
    difference is not visible from a single ``last_status``."""
    deliver_to: str | None = None
    """Optional delivery target for the job's result (e.g. a chat conversation id)."""
    metadata: dict[str, Any] = Field(default_factory=dict)
