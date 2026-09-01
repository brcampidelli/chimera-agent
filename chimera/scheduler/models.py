"""Data model for scheduled jobs (crons and event-triggered SOPs)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

Trigger = Literal["cron", "event", "webhook"]
CreatedBy = Literal["human", "agent"]

DispatchStatus = Literal["ok", "error", "timeout", "budget", "rejected"]
"""How a dispatch ended.

``rejected`` is the one that is not an error: the job RAN, produced work, and its own verify
command threw that work away. Nothing broke, so none of the exception paths fire — which is
exactly why it needed its own name. Measured on a real install: a nightly job whose gate rejected
every attempt and reverted every file reported ``ok`` with ``consecutive_failures: 0``, so the
Automation screen showed a green row over a job that had produced nothing, and the silence alarm
never saw it either. A gate that works and cannot be seen to have fired is not a signal."""


@dataclass(frozen=True)
class JobOutcome:
    """What a dispatch has to say about the job it just ran.

    ``ok=False`` means the work was REJECTED by the job's own gate, not that anything failed. A
    caller with no verdict to give — one running a job that declared no gate — returns a bare
    string instead and is read as ``ok``: absence of a verdict is not a failure, the same rule the
    reviewer and the verifier already follow when they abstain.
    """

    answer: str
    ok: bool = True
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
    disabled_by: str = ""
    """Who switched this job off: ``""`` (running, or off before this field existed), ``"human"``,
    or ``"brake"``.

    Not decoration on ``enabled``. A job somebody deliberately paused and a job the scheduler
    switched off after five straight failures are the same boolean and opposite facts, and
    :meth:`~chimera.scheduler.engine.Scheduler.failing` — the report whose entire job is to name
    broken jobs — filters on ``enabled``. Without this field the brake would HIDE its own findings
    in the one place someone goes to look for them."""

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
    workspace: str | None = None
    """Which folder this job works in. ``None`` = whatever root the process was started with.

    A schedule is written once and fires for months, so "wherever the app happens to be pointing
    when it goes off" is not a root anybody chose — and on a packaged desktop build the process root
    is the install directory. Found that way: *"list the project's files and say what changed
    today"* walked 4757 files of the app's own installation and was abandoned at 1800s, five nights
    running, having produced nothing.

    Recorded on the job rather than read at dispatch on purpose: the answer must not depend on which
    project the user happened to have open at 7am."""
    verify: str = ""
    """Shell command that decides whether a dispatch KEPT its work. Empty = no gate.

    This is what turns a scheduled job into a run the harness governs. With it set, the dispatch
    snapshots the workspace, runs the job, runs this command in that folder, and **reverts the
    workspace when it fails** — the same verify-or-revert every other surface has had and the one
    that runs most often did not.

    Opt-in, and it has to be. Most scheduled jobs are reports: summarise the day, check a price,
    post to a channel. Those change no files, and a gate that fails a run for changing no files
    would break every one of them — which is exactly the shape of a defect this project measured on
    its own release. So a job without a `verify` keeps today's behaviour and gains only the
    accounting; a job that edits code opts into the gate by naming the command that proves it.
    """
    max_attempts: int = 1
    """How many times one dispatch may try. 1 keeps today's behaviour: one shot, no retry.

    Above 1, a failed attempt is retried with the failure fed back — worth setting only alongside
    `verify`, because without a gate nothing can tell a failed attempt from a finished one.
    """
    metadata: dict[str, Any] = Field(default_factory=dict)
