"""Data model for scheduled jobs (crons and event-triggered SOPs)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Trigger = Literal["cron", "event", "webhook"]
CreatedBy = Literal["human", "agent"]

DispatchStatus = Literal["ok", "error", "timeout", "budget"]
"""What happened on the last dispatch — which is not the same question as whether one happened.

``last_run`` records the ATTEMPT: the scheduler sets it outside the try/except on purpose, so a job
that raises or overruns still has its schedule advanced and the tick moves on. That is right for
the scheduler and misleading for anyone reading the field to find out whether the job is working —
a job that has failed on every tick for a month has a `last_run` of a minute ago.

So the outcome is recorded beside it rather than folded into it. Two silences look identical from
`last_run` alone and need completely different responses: *nothing ran* means look at the daemon,
*everything ran and failed* means look at the job.

``budget`` is its own status and not an ``error`` for the same reason: a job refused because the day
is spent is not broken, and the two need opposite responses — one is a code fix, the other is a
number in the configuration. Folding them together would also let a spend refusal ride the
``consecutive_failures`` counter into looking like a job that has been failing for a week.
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
    max_usd: float | None = None
    """Dollar ceiling for ONE dispatch of this job. None = no per-job cap.

    Separate from the daily aggregate below: this one bounds a single runaway run (a retry loop on
    an expensive model), the other bounds the day. A job can hit its own cap every time and still be
    well inside the day's, which is the case where only this field says anything."""
    critical: bool = False
    """Exempt from the DAILY cap — never from its own.

    For the job whose absence costs more than its spend: a position guardian, a fills check. Without
    this, a daily cap tripped at 2 p.m. leaves the account unwatched until midnight, which is a worse
    outcome than the money it saved. Deliberately not the default: a job is ordinary until someone
    decides, in writing, that it is not."""
    metadata: dict[str, Any] = Field(default_factory=dict)
