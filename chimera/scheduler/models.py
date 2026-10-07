"""Data model for scheduled jobs (crons and event-triggered SOPs)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr


def kill_flag_path(home: Path, job_id: str) -> Path:
    """Where ``cron kill`` leaves its stop request for one job.

    A file, not shared memory: the daemon and the CLI are different processes on a VPS (or two
    containers on a host), and a file is the one channel both already trust for state — the same
    reason the heartbeat is a file. Written by the CLI, polled by the worker between steps,
    deleted by the dispatch it actually stops, so a flag can never outlive the run it was meant
    for and silence the one after it. Lives on the data model because both the engine (writer)
    and the job runner (reader) need it, and this module is the one neither has to apologise for
    importing.
    """
    return Path(home) / "scheduler" / f"kill.{job_id}.flag"

Trigger = Literal["cron", "event", "webhook", "once"]

CreatedBy = Literal["human", "agent"]

Notify = Literal["always", "on_change", "failures_only"]
"""When a job's answer is posted to its destination. See :attr:`CronJob.notify`."""

DispatchStatus = Literal["ok", "error", "timeout", "budget", "rejected", "cancelled"]
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
    cancelled: bool = False
    """The dispatch was stopped by the operator (``chimera cron kill``), not by its gate.

    A third fact beside ``ok``: a killed run is not evidence the job is broken, so it must not
    ride ``consecutive_failures`` into the brake — the same reasoning that stops a user
    cancellation from distilling an anti-pattern card. Nor is it a clean ``ok``: the work is a
    partial answer that was never verified, so it is not delivered anywhere as if it were one.
    The receipt in ``runs.jsonl`` carries ``ending="cancelled"``; this flag is how the dispatch
    layer knows to stay out of both the failure count and the delivery sink.
    """
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
    notify: Notify = "always"
    """When the answer is posted to :attr:`deliver_to`. The result file gets every answer whatever
    this says — it is the record, the destination is the interruption.

    * ``always`` (the default, and every job written before this field): every answer, as before,
      except the job's own "nothing new" reply (:data:`~chimera.scheduler.surface.NOTHING_NEW`).
    * ``on_change``: skipped when the answer is the same as the last one delivered for this job,
      after whitespace is normalised. "One ping per state, not per tick": a monitor that finds the
      same thing every five minutes was posting it every five minutes.
    * ``failures_only``: only a dispatch that did not succeed — its gate rejected the work, or it
      could not run or finish.

    A rejected run's answer is never suppressed by ``on_change`` either: a job that breaks the same
    way twice is still broken, and silence is how a broken monitor reads as a quiet day.

    A run that could not run or finish (``error``, ``timeout``, ``budget``, or the brake switching
    the job off) is not an answer and is not governed by this field: it reaches :attr:`deliver_to`
    as a short failure notice under EVERY mode — once per outage and per failure kind, and once
    when it has run again for a while (:attr:`failure_notice`), never with the error text. With
    ``CHIMERA_CRON_NOTIFY_FAILURES`` off nothing of it is posted, so a ``failures_only`` job then
    hears only about rejected runs.

    Applies to cron and event jobs. A webhook job answers through the chat gateway, which does not
    read this field, so :meth:`~chimera.scheduler.engine.Scheduler.schedule_webhook` refuses
    anything but ``always``."""
    tools: list[str] | None = None
    """The tools this job may use, by name. ``None`` (the default) = every tool, as before.

    The owner's list, not a router's guess: a report job that reads a feed and posts a summary needs
    three tools, and carrying the other twenty-odd schemas costs thousands of characters per step and
    offers the model what the job has no business doing. A tool not on the list is REMOVED from the
    registry (:func:`~chimera.governance.allowlist.restrict_registry`), which a sentence in the
    prompt cannot do. An empty list grants nothing. Narrows the deployment's own allowlist, never
    widens it.

    Applies to cron and event jobs only. A webhook job runs through the chat gateway with the
    gateway's registry, so a list here would not be enforced: ``schedule_webhook`` refuses it, and
    the webhook handler refuses to run a job that has one (written into jobs.json by hand)."""
    last_delivered_hash: str | None = None
    """Fingerprint of the last answer delivered for this job — what ``notify="on_change"`` compares
    against. Kept on the job because the job is the only state that survives a restart."""
    failure_notice: str = ""
    """The outage the owner has been told about at :attr:`deliver_to`, by its latest kind —
    ``error``, ``timeout``, ``budget`` or ``brake`` — or ``""`` when no outage is open: they were
    never told of one, or were told it ended. Written by
    :func:`~chimera.scheduler.delivery.make_failure_notifier`, never by the engine.

    The memory that makes a failure notice one post per change of state rather than one per tick:
    a job on ``*/5`` whose provider is down for an afternoon is one message when it breaks and one
    when it comes back, not fifty. On the job, like :attr:`last_delivered_hash`, because a restart
    in the middle of an outage must not announce the same outage again."""
    failure_notice_told: list[str] = Field(default_factory=list)
    """Every failure kind already announced in the open outage. A kind the owner has heard about
    is not news the second time it comes round: a job alternating ``error`` and ``budget`` posted
    on every tick while the notice only remembered the latest kind. Emptied when the outage ends."""
    failure_notice_healthy: int = 0
    """Runs in a row that finished (``ok`` or ``rejected``) since the open outage's last failure.
    The outage ends — and "running again" is posted — only at
    :data:`~chimera.scheduler.delivery.RECOVERY_RUNS`: one success between two errors is what a
    flapping provider looks like, and announcing it as a recovery turned every flap into two posts."""
    _answer_posted: bool = PrivateAttr(default=False)
    """Set by the result sink when THIS run's answer reached :attr:`deliver_to`. Not persisted: it
    describes one dispatch, read by the failure notifier in the same tick and cleared there. It is
    how the notice knows the channel already saw the job run again and need not be told twice."""
    metadata: dict[str, Any] = Field(default_factory=dict)
