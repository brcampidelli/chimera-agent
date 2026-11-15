"""Data model for scheduled jobs (crons and event-triggered SOPs)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr, field_validator

_log = logging.getLogger(__name__)


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

CatchUp = Literal["once", "skip"]
"""What a job does when its time passed while nothing was running. See :attr:`CronJob.catch_up`."""

#: The permission modes a scheduled job may be given: the Code screen's, WITHOUT ``bypass``.
#:
#: Kept here as a literal tuple, and not imported from ``chimera.api.posture``, so the data model
#: stays importable without the API package; ``tests/test_scheduled_permission_mode.py`` holds the
#: two equal. ``bypass`` is not a mode a job can name, ever: nobody is watching a scheduled run, so
#: "ignore permissions" there would be the one switch that has no one to catch its mistake.
JOB_PERMISSION_MODES: tuple[str, ...] = ("manual", "accept_edits", "plan", "auto", "dont_ask")

#: The most steps one scheduled dispatch may be given. A ceiling and not a default: the serve loop's
#: own ``--max-steps`` (6) is what a job inherits, and a job that names more is the owner saying
#: "this one needs room". Unattended, an unbounded number is a loop nobody is watching.
MAX_JOB_STEPS = 40

_MODEL_SLUG_MAX = 200


def validate_run_config(model: str, effort: str, max_steps: int | None) -> None:
    """Refuse a per-job ``model`` / ``effort`` / ``max_steps`` the dispatch could not honour.

    Raises ``ValueError`` with a sentence the caller can show as it stands. Empty ``model`` / empty
    ``effort`` / ``None`` ``max_steps`` mean "inherit from the serve loop" and are always valid.

    ``model`` follows the rule the rest of the install already uses for model slugs: anything the
    gateway can route (``provider/model``, a LiteLLM or OpenRouter slug) or a catalog slug, with no
    whitespace. It is NOT checked against the catalog alone — the catalog is a list of suggestions,
    and ``weak_model`` / ``default_model`` accept any slug — but a name that is plainly not a slug
    (spaces, no provider) is refused here, at the keyboard, instead of failing at 07:00 with nobody
    to read the error. The reasoning variant belongs in ``effort``, so a ``#high`` suffix on the
    model is refused: two places to say it is how the two disagree.
    """
    # Imported here: the data model stays importable without the provider package.
    from chimera.providers.catalog import CATALOG, REASONING_EFFORTS, split_effort

    slug = model.strip()
    if slug:
        if split_effort(slug)[1] is not None:
            raise ValueError("model must not carry a #effort suffix; set effort separately")
        known = {entry.slug for entry in CATALOG}
        shaped = (
            len(slug) <= _MODEL_SLUG_MAX
            and slug.isprintable()
            and not any(ch.isspace() for ch in slug)
            and "/" in slug.strip("/")
        )
        if slug not in known and not shaped:
            raise ValueError(
                f"model {model!r} is not a model slug (expected provider/model, "
                "e.g. openrouter/vendor/model)"
            )
    if effort and effort not in REASONING_EFFORTS:
        raise ValueError(f"effort must be one of {', '.join(REASONING_EFFORTS)} (or empty)")
    if max_steps is not None and (
        isinstance(max_steps, bool) or not 1 <= max_steps <= MAX_JOB_STEPS
    ):
        raise ValueError(f"max_steps must be an integer from 1 to {MAX_JOB_STEPS}")


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
    _delivery_failed: bool = PrivateAttr(default=False)
    """Set by the result sink when THIS run's answer was posted and the destination did not accept it.
    Not persisted: it describes one dispatch. The dispatch reads it to end the run as a failure
    rather than as ``ok``, so a destination that is down rides ``consecutive_failures`` into the
    brake instead of being retried, and re-asked of the model, every tick for ever."""
    catch_up: CatchUp = "once"
    """What happens when the job's time passed while the app or the service was off.

    * ``once`` (the default, and every job written before this field): the job runs ONE time when
      the scheduler is back, if the latest missed time is no more than seven days old. However many
      times were missed, it never runs more than once — a daily job off for a weekend is one run
      on Monday, not three.
    * ``skip``: the missed times are recorded in the history and nothing is run; the schedule
      resumes at its next time.

    A missed time older than seven days is skipped under either value (``missed_too_old``): work
    that was due last month is not what the owner wants to find running on a Tuesday."""
    permission_mode: str = ""
    """The permission mode this job runs under, or ``""`` (none chosen: the job runs as it always
    did). One of :data:`JOB_PERMISSION_MODES`.

    Nobody watches a scheduled run, so the mode is a CEILING and never more than the install's own
    posture: the owner's screen refuses a mode wider than the global one, and the run narrows to
    the global posture again if Settings were tightened after the job was written. A mode that
    would ask a person (``manual``, ``accept_edits``) refuses the action instead — nobody can
    answer, and the run never approves for itself. Only the owner's screen sets it
    (``bridge_routes.OWNER_DECISION_ROUTES``)."""
    model: str = ""
    """The model this job runs on, or ``""`` (inherit the serve loop's, as every job did before).

    A scheduled report that reads a feed and posts a summary does not need the model the owner chats
    with, and every job paying the top rung's price is the default only because nothing could say
    otherwise. Validated by :func:`validate_run_config`. Never carries a ``#effort`` suffix: that is
    :attr:`effort`."""
    effort: str = ""
    """Reasoning effort for this job (``low``, ``medium``, ``high``), or ``""`` (the model as it is).
    Sent as the slug's ``#effort`` variant, the same way every other surface asks for it."""
    max_steps: int | None = None
    """Tool-calling steps this job may take per attempt, or ``None`` (inherit the serve loop's).
    Between 1 and :data:`MAX_JOB_STEPS`."""
    worktree: bool = False
    """Run in a new git worktree of the job's folder instead of in the folder itself.

    Removed when the run ended with nothing changed and nothing committed; kept, with its path in
    the history, when it left work behind. ``False`` is what every job did before this field."""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model", "effort", mode="before")
    @classmethod
    def _text_or_inherit(cls, value: Any, info: Any) -> Any:
        """A hand-edited ``jobs.json`` with a non-text ``model`` / ``effort`` keeps the job.

        The store drops an entry the model refuses, and a dropped job is a job that stops running
        and is then erased by the next write. A value of the wrong TYPE is read as "inherit" and
        said so; a text value that is not a valid model or effort is kept as written and is refused
        (with a warning, inheriting) when the job runs, where the reason can be shown.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        _log.warning("cron job: %s=%r is not text; the job inherits instead", info.field_name, value)
        return ""

    @field_validator("max_steps", mode="before")
    @classmethod
    def _steps_or_inherit(cls, value: Any) -> Any:
        """Same rule for ``max_steps``: not a whole number -> ``None`` (inherit), with a warning."""
        if value is None:
            return None
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        _log.warning("cron job: max_steps=%r is not a whole number; the job inherits instead", value)
        return None


def webhook_unsupported(job: CronJob) -> list[str]:
    """The run-configuration fields a webhook job carries but the webhook path does not read.

    A webhook runs through the chat gateway with the gateway's own model and step budget, so a job
    that names its own would run on something else than it says. ``schedule_webhook`` refuses to
    write them; this is for the job that got them by a hand edit of ``jobs.json``.
    """
    named = {"model": bool(job.model), "effort": bool(job.effort), "max_steps": job.max_steps is not None}
    return [name for name, present in named.items() if present]


def refuse_webhook_overrides(job: CronJob) -> None:
    """Raise when a webhook job names a model, effort or step budget the webhook path would ignore.

    Fail closed, as for ``tools``/``notify``: running it on the gateway's own values would run it on
    something else than it says.
    """
    unread = webhook_unsupported(job)
    if unread:
        raise ValueError(
            f"webhook job {job.name!r} sets {', '.join(unread)}, which the webhook path does not "
            "apply; refusing to run it with other values than declared"
        )
