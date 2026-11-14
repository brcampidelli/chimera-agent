"""Scheduling engine: due-job computation and event dispatch.

All time is passed in explicitly (``now`` as epoch seconds), so behaviour is fully
deterministic and testable — there is no hidden ``sleep`` or wall-clock read here.
A separate runner (CLI/daemon) supplies the real clock.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

from chimera.concurrency import call_with_deadline
from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler.history import HISTORY_FILENAME, CronHistory, HistoryRow, Outcome
from chimera.scheduler.models import (
    JOB_PERMISSION_MODES,
    CatchUp,
    CreatedBy,
    CronJob,
    DispatchStatus,
    Notify,
    kill_flag_path,
)
from chimera.scheduler.store import CronStore
from chimera.telemetry import get_logger

_log = get_logger("scheduler.engine")

#: Consecutive failures after which a job is switched off.
#:
#: The counter was already exact — ``_record`` deliberately excludes a budget refusal, because the
#: job never ran — and nothing acted on it: ``disable()`` had two callers and both are a person.
#: So a job that started failing at 02:00 kept firing every tick until somebody read a table. The
#: daily spend cap bounds the money but is the wrong instrument: it stops after paying, not on
#: detecting.
#:
#: Five rather than two: a provider hiccup is not a broken job, and switching one off for a
#: transient error is its own outage. This is a brake and not a delete — ``last_status``,
#: ``last_error`` and the counter all survive, and ``cron enable`` puts the job back.
FAIL_LIMIT = 5

#: Share of a schedule's own interval that a job may be delayed, and the ceiling on that delay.
#:
#: Bounded twice on purpose. The fraction keeps a job written ``* * * * *`` from sliding past the
#: next minute — an offset larger than the interval does not make a job late, it silently makes it
#: a two-minute job. The absolute cap keeps a daily job from inheriting a tenth of a day: "every
#: morning · 7h" firing at half past nine is a different promise from the one the screen made.
JITTER_FRAC = 0.1
JITTER_CAP_S = 300.0


#: How old the latest MISSED time may be and still be run when the scheduler comes back: seven days.
#: Measured against the most recent time that was missed, not the oldest — a daily job off for ten
#: days has a missed time from yesterday and runs once, while a monthly job whose only missed time
#: was ten days ago does not. Past it the time is recorded as ``missed_too_old`` and skipped.
CATCH_UP_MAX_AGE_S = 7 * 24 * 3600.0

#: How late a single time may run before it counts as missed. A dispatch is sequential, so a job
#: behind a slow neighbour is minutes late with the scheduler perfectly alive; that is not a missed
#: time, and under ``catch_up="skip"`` it must not be thrown away. Ten minutes is above anything a
#: tick, the jitter (which is part of ``next_run`` already) or one ordinary job can cost, and far
#: below the time a closed app takes to be noticed.
CATCH_UP_GRACE_S = 600.0

#: How long between two ticks the scheduler is still taken to have been alive the whole time. Dispatch
#: is sequential, so one slow job holds back every job after it, and the next tick starts only when it
#: is done: a gap shorter than this is a tick that took long (the default job timeout is 30 minutes),
#: not an outage, and the jobs behind it are late and not missed. A longer gap, or no earlier tick at
#: all, is read as the scheduler having been off. The price: an outage shorter than this is not seen
#: as one, and the job simply runs once, late.
CATCH_UP_ALIVE_GAP_S = 1800.0

#: How many missed times are counted before the count is given up on. A ``* * * * *`` job off for a
#: month has forty thousand; the answer to "how many" is "a lot", not an exact number worth a loop.
MAX_MISSED_SCAN = 1000

#: How many of them get a line each in the history. The rest are said in one line's detail, so a
#: long outage cannot bury the history of every other job.
MAX_HISTORY_ROWS_PER_CATCH_UP = 50


@dataclass(frozen=True)
class _CatchUp:
    """What the scheduler found when a job's time had passed with nothing running."""

    times: list[float]
    """The missed scheduled times, oldest first (at most :data:`MAX_MISSED_SCAN`)."""
    capped: bool
    latest: float
    """The most recent missed time: the one a catch-up run stands in for."""
    skip: bool
    reason: str


def _heartbeat_at(path: Path) -> float | None:
    """When the daemon last ticked, from its heartbeat file, or None (no file, unreadable)."""
    try:
        beat = json.loads(path.read_text(encoding="utf-8"))
        # The END of the daemon's last tick when it wrote one, else the start (an older heartbeat).
        at = beat.get("ended_at", beat.get("at"))
    except (OSError, ValueError, AttributeError):
        return None
    return float(at) if isinstance(at, (int, float)) else None


def _latest_at_or_before(cron_expr: str, now: float) -> float:
    """The latest scheduled time not after ``now``, in the machine's zone like :func:`_next_after`."""
    base = datetime.fromtimestamp(now + 1.0, tz=UTC).astimezone(None)
    return float(croniter(cron_expr, base).get_prev(float))


#: ``CronJob.metadata`` key set once the history has said a switched-off job's time passed. One line
#: per outage and not one per time: a ``* * * * *`` job someone paused would otherwise add a line a
#: minute for as long as it stays paused.
_DISABLED_NOTED = "history_disabled_noted"


def _clean_tools(tools: list[str] | None) -> list[str] | None:
    """A job's tool list, trimmed and without repeats, in the order given. None stays None: "no
    list" (every tool) and "an empty list" (no tool) are opposite instructions."""
    if tools is None:
        return None
    return list(dict.fromkeys(name.strip() for name in tools if name.strip()))


def _jitter(key: str, period: float) -> float:
    """A stable offset in ``[0, min(JITTER_FRAC * period, JITTER_CAP_S))`` derived from ``key``.

    Derived, not drawn. A random offset is redrawn on every restart, so the spread a deployment
    converged on is lost each time the container comes up, and two jobs that happened to collide
    keep colliding on a new pair of numbers. A hash of the id gives the same job the same slot
    forever and different jobs different slots, with nothing stored.

    An empty key means no offset, which is what keeps every caller that does not ask for a spread
    on exactly the arithmetic it had.
    """
    if not key or period <= 0:
        return 0.0
    span = min(JITTER_FRAC * period, JITTER_CAP_S)
    fraction = int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) / 0x1_0000_0000
    return fraction * span


def _dispatch_bounded(
    job: CronJob, dispatch: Callable[[CronJob], DispatchStatus | None], timeout: float | None
) -> DispatchStatus | None:
    """Run ``dispatch(job)``, raising :class:`TimeoutError` if it overruns ``timeout``.

    ``None`` runs it inline (the previous, unbounded behaviour) so nothing pays for a thread when
    no deadline is set. With a deadline the job runs on a daemon thread and an overrun is abandoned
    — a running job cannot be killed in Python, so waiting for it would reproduce the very stall the
    timeout exists to prevent.

    That abandonment used to be a lie: this ran on a ``ThreadPoolExecutor``, whose threads are joined
    at interpreter exit no matter what ``shutdown(wait=False)`` says, so a stuck job let the tick
    finish and then held the process open until it unstuck. See :mod:`chimera.concurrency`.
    """
    return call_with_deadline(lambda: dispatch(job), timeout)


def _next_after(
    cron_expr: str,
    after_epoch: float,
    *,
    jitter_key: str = "",
    timezone: ZoneInfo | None = None,
) -> float:
    """The next epoch matching ``cron_expr``, read in the requested or machine time zone.

    ``0 7 * * *`` means seven in the morning where the machine is, which is what every crontab has
    always meant and what a screen offering "every morning · 7h" is promising. This used to pin the
    base to UTC, so on a desktop four hours west that job fired at 03:00 — measured on a real
    install, where the job's own record read ``last_run 03:00`` under a schedule of ``0 7``.

    A server in UTC is unaffected: local time IS UTC there, which is why the defect could sit in a
    codebase whose deployment target is a container. It only shows on the machine of a person.

    Built through UTC rather than straight to local, which is not stylistic. A naive
    ``fromtimestamp(x)`` west of Greenwich puts a small ``x`` in 1969, and asking Windows for the
    local offset of a pre-1970 instant raises ``OSError: [Errno 22]``. The scheduler's own tests
    pass ``now=60``, so the naive form took the whole Windows suite down — on Linux and on WSL it is
    fine, which is why it reached CI. An aware datetime converts by arithmetic and never asks.

    ``jitter_key`` spreads jobs that share a boundary. Dispatch is sequential, so fifteen jobs
    written ``0 * * * *`` do not merely run slowly at ``:00`` — they delay every later tick behind
    them. The offset only ever DELAYS, and is bounded by the schedule's own interval (see
    :func:`_jitter`); an empty key returns the exact boundary, unchanged.
    """
    base = datetime.fromtimestamp(after_epoch, tz=UTC).astimezone(timezone)
    ticker = croniter(cron_expr, base)
    nxt = float(ticker.get_next(float))
    if not jitter_key:
        return nxt
    # The interval is measured from the schedule itself rather than assumed, so `*/5` and `0 7 * * *`
    # each get an offset proportional to what they actually promise.
    period = float(ticker.get_next(float)) - nxt
    return nxt + _jitter(jitter_key, period)


def next_firings(
    cron_expr: str,
    now: float,
    *,
    first_run: float | None = None,
    count: int = 3,
    timezone: ZoneInfo | None = None,
) -> list[float]:
    """Return upcoming matching epochs, reusing the engine's persisted first firing when possible.

    ``croniter`` remains the sole schedule parser; the first epoch is the same calculation used to
    populate ``CronJob.next_run``. Subsequent epochs advance from each prior match, preserving
    croniter's local-time and DST semantics.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    if count == 0:
        return []
    first = (
        first_run
        if first_run is not None and first_run > now
        else _next_after(cron_expr, now, timezone=timezone)
    )
    firings = [first]
    while len(firings) < count:
        firings.append(_next_after(cron_expr, firings[-1], timezone=timezone))
    return firings


class Scheduler:
    """Creates, lists and dispatches scheduled jobs over a :class:`CronStore`."""

    def __init__(
        self, store: CronStore, *, fail_limit: int = FAIL_LIMIT, jitter: bool = True
    ) -> None:
        if fail_limit < 1:
            # A limit of zero switches off a job that has never failed, which reads as "the
            # scheduler is broken" to everyone. Refusing here is louder than a job that vanishes on
            # its first tick.
            raise ValueError(f"fail_limit must be at least 1, got {fail_limit}")
        self.store = store
        self.history = CronHistory(store.path.parent / HISTORY_FILENAME)
        #: When the previous tick of THIS process ENDED (see :meth:`note_tick_end`); before it has
        #: had one, the daemon's heartbeat file stands in (see :meth:`_scheduler_was_alive`).
        self._last_tick: float | None = None
        # Jobs whose dispatch is in progress, and the (job, time) pairs already said to have been
        # refused for it, so a long run is one line in the history and not one per tick.
        self._running: dict[str, object] = {}
        self._noted_running: set[tuple[str, float | None]] = set()
        self.fail_limit = fail_limit
        #: Off for a caller that needs the exact boundary — a market open, a report due at midnight.
        #: A spread that cannot be switched off is a spread people work around.
        self.jitter = jitter

    def _jitter_key(self, job: CronJob) -> str:
        return job.id if self.jitter else ""

    def _brake(self, job: CronJob) -> None:
        """Switch a job off once it has failed ``fail_limit`` times in a row.

        Called after the outcome is recorded, on every dispatch path. No delivery from here: the
        engine takes no clock and no I/O, and the decision is visible everywhere the counter already
        is — ``cron list``, ``cron doctor`` and ``/api/features`` all read this job. The job's chat
        destination is told by the daemon, which reads ``disabled_by`` after the tick
        (:func:`~chimera.scheduler.delivery.make_failure_notifier`).
        """
        if not job.enabled or job.consecutive_failures < self.fail_limit:
            return
        job.enabled = False
        job.disabled_by = "brake"
        _log.error(
            "cron job %s (%s) switched off after %d consecutive failures; last error: %s",
            job.name, job.id, job.consecutive_failures, job.last_error or "(none recorded)",
        )
        self.store.add(job)

    def schedule_cron(
        self,
        name: str,
        cron_expr: str,
        action: str,
        *,
        now: float,
        created_by: CreatedBy = "human",
        workspace: str | None = None,
        deliver_to: str | None = None,
        verify: str = "",
        max_attempts: int = 1,
        notify: Notify = "always",
        tools: list[str] | None = None,
        catch_up: CatchUp = "once",
        permission_mode: str = "",
        worktree: bool = False,
    ) -> CronJob:
        """Register a job fired by a cron expression.

        verify: Shell command that decides whether this job's dispatch KEPT its work; empty means
            no gate, which is today's behaviour. Accepted here rather than only on the model
            because a field nothing can set is a field nobody has: `CronJob` has carried `verify`
            and `max_attempts` since the harness landed, and neither the HTTP route nor the CLI
            could write them, so the gate could never arm for any user.
        max_attempts: How many times one dispatch may try. Worth raising only alongside `verify` —
            without a gate nothing can tell a failed attempt from a finished one.
        notify: When the answer is posted to the job's destination (`CronJob.notify`); `always`
            is today's behaviour.
        tools: The only tools the job may use (`CronJob.tools`); None keeps every tool.
        catch_up: What to do about a time missed while nothing ran (`CronJob.catch_up`); ``once``
            is what every job did before the field existed.
        permission_mode: The mode this job runs under (`CronJob.permission_mode`), or empty for
            none. Whether it is wider than the install's posture is NOT decided here — a scheduler
            has no settings — but where the owner's screen sets it, and again when the job runs.
        worktree: Run in a throwaway git worktree of the job's folder (`CronJob.worktree`).
        """
        if not croniter.is_valid(cron_expr):
            raise ValueError(f"invalid cron expression: {cron_expr!r}")
        if permission_mode and permission_mode not in JOB_PERMISSION_MODES:
            raise ValueError(
                f"permission_mode must be one of {', '.join(JOB_PERMISSION_MODES)} (or empty), "
                f"got {permission_mode!r}"
            )
        # Minted before the job, because the job's own id is what its schedule offset is derived
        # from — the first `next_run` has to land in the same slot every later one will.
        job_id = uuid.uuid4().hex[:8]
        job = CronJob(
            id=job_id,
            name=name,
            trigger="cron",
            schedule=cron_expr,
            action=action,
            created_by=created_by,
            # Defend the "self-learned crons start disabled" invariant at the boundary, not just in
            # the learner: an agent-created cron must not fire until a human enables it.
            enabled=created_by != "agent",
            next_run=_next_after(cron_expr, now, jitter_key=job_id if self.jitter else ""),
            workspace=workspace,
            deliver_to=deliver_to,
            verify=verify,
            max_attempts=max(1, max_attempts),
            notify=notify,
            tools=_clean_tools(tools),
            catch_up=catch_up,
            permission_mode=permission_mode,
            worktree=worktree,
        )
        self.store.add(job)
        return job

    def schedule_event(
        self,
        name: str,
        event: str,
        action: str,
        *,
        created_by: CreatedBy = "human",
        deliver_to: str | None = None,
        verify: str = "",
        max_attempts: int = 1,
        notify: Notify = "always",
        tools: list[str] | None = None,
    ) -> CronJob:
        """Register a job fired by a named event.

        deliver_to: Chat webhook the answer is posted to. Accepted here because `cron fire`
            dispatches through the same sink as the daemon, so an event job can deliver; a field
            only a hand-edited jobs.json could set is a field nobody has.

        verify: Shell command that decides whether this job's dispatch KEPT its work; empty means
            no gate, which is today's behaviour. Accepted here rather than only on the model
            because a field nothing can set is a field nobody has: `CronJob` has carried `verify`
            and `max_attempts` since the harness landed, and neither the HTTP route nor the CLI
            could write them, so the gate could never arm for any user.
        max_attempts: How many times one dispatch may try. Worth raising only alongside `verify` —
            without a gate nothing can tell a failed attempt from a finished one.
        notify: When the answer is posted to the job's destination (`CronJob.notify`); `always`
            is today's behaviour.
        tools: The only tools the job may use (`CronJob.tools`); None keeps every tool.
        """
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="event",
            schedule=event,
            action=action,
            created_by=created_by,
            enabled=created_by != "agent",  # agent-created triggers start disabled (same invariant)
            deliver_to=deliver_to,
            verify=verify,
            max_attempts=max(1, max_attempts),
            notify=notify,
            tools=_clean_tools(tools),
        )
        self.store.add(job)
        return job

    def schedule_webhook(
        self,
        name: str,
        hook: str,
        action: str,
        *,
        created_by: CreatedBy = "human",
        verify: str = "",
        max_attempts: int = 1,
        notify: Notify = "always",
        tools: list[str] | None = None,
    ) -> CronJob:
        """Register a job fired by an inbound HTTP POST to ``/webhook/<hook>``.

        A webhook job is the one most likely to edit code on somebody else's schedule, so it takes
        the gate for the same reason a cron does.

        verify: Shell command that decides whether this job's dispatch KEPT its work; empty means
            no gate, which is today's behaviour. Accepted here rather than only on the model
            because a field nothing can set is a field nobody has: `CronJob` has carried `verify`
            and `max_attempts` since the harness landed, and neither the HTTP route nor the CLI
            could write them, so the gate could never arm for any user.
        max_attempts: How many times one dispatch may try. Worth raising only alongside `verify` —
            without a gate nothing can tell a failed attempt from a finished one.
        notify: Must be ``always``. See below.
        tools: Must be None. See below.

        Raises:
            ValueError: ``tools`` or a ``notify`` other than ``always``. A webhook job is dispatched
                by ``chimera serve`` through the chat gateway, with the gateway's own registry and
                reply path — not through the scheduled-run path that applies ``CronJob.tools``,
                ``notify`` and the unattended-run note. Storing them would print ``tools=read_file``
                in ``cron list`` over a run that holds every tool: a fence that reads as enforced
                and is not, which is worse than no fence. Refused until the webhook path honours
                them.
        """
        if tools is not None or notify != "always":
            raise ValueError(
                "a webhook job cannot take --tools or --notify yet: it runs through the chat "
                "gateway, which does not apply either, so the job would hold every tool and post "
                "every answer while its listing said otherwise"
            )
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="webhook",
            schedule=hook,
            action=action,
            created_by=created_by,
            verify=verify,
            max_attempts=max(1, max_attempts),
            notify=notify,
            tools=_clean_tools(tools),
        )
        self.store.add(job)
        return job

    def jobs_for_webhook(self, hook: str) -> list[CronJob]:
        """Enabled webhook jobs registered for ``hook``."""
        return [
            job
            for job in self.store.list()
            if job.enabled and job.trigger == "webhook" and job.schedule == hook
        ]

    def fire_webhook(self, hook: str, now: float, dispatch: Callable[[CronJob], DispatchStatus | None]) -> list[CronJob]:
        """Dispatch every job registered for ``hook`` (an inbound webhook). Returns those run."""
        ran: list[CronJob] = []
        for job in self.jobs_for_webhook(hook):
            try:
                dispatch(job)
            except Exception as exc:  # a failing job must not break the server
                _log.warning("webhook job %s failed: %s", job.id, exc)
            self.mark_ran(job, now)
            ran.append(job)
        return ran

    def schedule_once(
        self, name: str, run_at: float, action: str, *, now: float,
        workspace: str | None = None, deliver_to: str | None = None,
    ) -> CronJob:
        """Persist one approved, one-shot action for the scheduler daemon to dispatch."""
        if run_at <= now:
            raise ValueError("run_at must be in the future")
        job = CronJob(
            id=uuid.uuid4().hex[:8], name=name, trigger="once", schedule="once",
            action=action, created_by="human", enabled=True, next_run=run_at,
            workspace=workspace, deliver_to=deliver_to,
        )
        self.store.add(job)
        return job

    def due(self, now: float) -> list[CronJob]:
        """Enabled cron or one-shot jobs whose ``next_run`` is at or before ``now``."""
        return [
            job
            for job in self.store.list()
            if job.enabled
            and job.trigger in {"cron", "once"}
            and job.next_run is not None
            and job.next_run <= now
        ]

    def overdue(self, now: float, *, grace: float = 0.0) -> list[tuple[CronJob, float]]:
        """Enabled cron jobs whose time passed more than ``grace`` ago, and by how much.

        This is the silence nothing else in the project can see. Every honesty mechanism here sits
        downstream of a run having happened — the verifier judges a result, the diff gate measures a
        change, the receipt names who approved it. None of them gets a turn when the run never
        occurred, and a schedule that produces no receipt reads as a schedule with nothing due.

        The expectation was already on disk: ``next_run`` is computed from the cron expression and
        only moves when the job fires. What was missing was somebody asking.

        **What this cannot do, stated because the alternative is implying otherwise.** It is a
        question, not a watcher. Nothing here notices while the process is down, for the same reason
        a crashed process cannot log its own crash — a check that lives in the daemon is dead when
        the daemon is. What it gives you is an honest answer the moment anything asks: the CLI, the
        app, the next start. A real watcher needs its own clock and its own liveness, and that is a
        separate decision (issue #26) rather than something to fake here.

        ``grace`` exists because "due a second ago" is a tick in progress, not a miss. Pass the
        deployment's tick interval, or a multiple of it.
        """
        late: list[tuple[CronJob, float]] = []
        for job in self.store.list():
            if not job.enabled or job.trigger != "cron" or job.next_run is None:
                continue
            behind = now - job.next_run
            if behind > grace:
                late.append((job, behind))
        return sorted(late, key=lambda pair: pair[1], reverse=True)

    def failing(self, *, at_least: int = 1) -> list[CronJob]:
        """Enabled jobs that have failed ``at_least`` times in a row since their last success.

        The second silence, and the one that looks healthiest: these jobs ARE running. ``last_run``
        is recent, the daemon is alive, the schedule is advancing — and every dispatch has lost.
        Told apart from :meth:`overdue` because the responses have nothing in common: one says look
        at the daemon, the other says look at the job.

        A job the brake switched off is still reported, and that exception is the whole reason
        ``disabled_by`` exists. Filtering on ``enabled`` alone would make the brake hide its own
        findings in the one place someone goes to look for them: the most broken job on the machine
        would be the only one missing from the list of broken jobs. A job a PERSON paused stays out
        — they know.
        """
        return [
            job
            for job in self.store.list()
            if (job.enabled or job.disabled_by == "brake")
            and job.consecutive_failures >= at_least
        ]

    def jobs_for_event(self, event: str) -> list[CronJob]:
        """Enabled event jobs registered for ``event``."""
        return [
            job
            for job in self.store.list()
            if job.enabled and job.trigger == "event" and job.schedule == event
        ]

    def enable(self, job_id: str, *, now: float) -> CronJob:
        """Enable a job; for cron jobs, (re)compute the next run from ``now``."""
        job = self.store.get(job_id)
        job.enabled = True
        # The counter is cleared here and only here: re-enabling is somebody saying they dealt with
        # the cause, and leaving it at the limit would switch the job off again on its next failure
        # rather than after five — turning the brake into a one-strike rule for anything it has
        # ever caught.
        job.consecutive_failures = 0
        job.disabled_by = ""
        job.metadata.pop(_DISABLED_NOTED, None)
        if job.trigger == "cron":
            job.next_run = _next_after(job.schedule, now, jitter_key=self._jitter_key(job))
        self.store.add(job)
        return job

    def disable(self, job_id: str) -> CronJob:
        """Switch a job off by hand. Recorded as a human decision, so `failing()` stays quiet about
        it — the person who paused it does not need a report telling them it is paused."""
        job = self.store.get(job_id)
        job.enabled = False
        job.disabled_by = "human"
        job.metadata.pop(_DISABLED_NOTED, None)
        self.store.add(job)
        return job

    def mark_ran(self, job: CronJob, now: float) -> None:
        job.last_run = now
        if job.trigger == "cron":
            job.next_run = _next_after(job.schedule, now, jitter_key=self._jitter_key(job))
        elif job.trigger == "once":
            job.enabled = False
            job.next_run = None
        self.store.add(job)

    # ---- history ---------------------------------------------------------------------------------

    def _note(
        self,
        job: CronJob,
        at: float,
        outcome: Outcome,
        reason: str,
        *,
        status: str = "",
        scheduled_for: float | None = None,
        detail: str = "",
    ) -> None:
        """One line in the history. Never raises: a record must not cost the tick that made it."""
        self.history.append(
            HistoryRow(
                at=at,
                job_id=job.id,
                name=job.name,
                outcome=outcome,
                reason=reason,
                status=status,
                scheduled_for=scheduled_for,
                detail=detail[:300],
            )
        )

    def _missed_times(self, job: CronJob, now: float) -> tuple[list[float], bool]:
        """Every scheduled time from ``job.next_run`` up to ``now``, oldest first, and whether the
        count was cut at :data:`MAX_MISSED_SCAN`. The first is ``next_run`` itself (jitter included,
        because that is the time the job was actually waiting for) and the rest follow the
        expression."""
        if job.next_run is None:
            return [], False
        times = [job.next_run]
        while len(times) < MAX_MISSED_SCAN:
            nxt = _next_after(job.schedule, times[-1])
            if nxt > now:
                return times, False
            times.append(nxt)
        return times, True

    def note_tick_end(self, at: float) -> None:
        """Record when a tick FINISHED, on the caller's clock (the daemon calls this after
        ``run_due``). The engine has no clock of its own, and a tick that ran sequential jobs for 40
        minutes ends 40 minutes after it began: measured from its start, the next tick would read
        that as the scheduler having been off, and ``skip`` would discard jobs the live daemon was
        only late with."""
        if self._last_tick is None or at > self._last_tick:
            self._last_tick = at

    def _scheduler_was_alive(self, now: float) -> bool:
        """Whether the scheduler ticked recently enough that a late job is behind a slow neighbour,
        not behind an outage. Read from this process's previous tick, or, before it has had one, the
        daemon's heartbeat file; no evidence at all reads as "was off", which is what a first start
        after the app was closed looks like."""
        before = self._last_tick
        if before is None:
            before = _heartbeat_at(self.store.path.parent / "heartbeat.json")
        return before is not None and 0.0 <= now - before <= CATCH_UP_ALIVE_GAP_S

    def _plan_catch_up(self, job: CronJob, now: float, alive: bool) -> _CatchUp | None:
        """Whether this job's time passed without a run, and what to do about it. None = on time.

        A time counts as missed when more than one scheduled time has come round since the job last
        had its turn, or when the one time is later than :data:`CATCH_UP_GRACE_S`. Either way the
        answer is ONE decision for the job: run it once for the latest missed time, or skip them
        all. Never one run per missed time.
        """
        if alive or job.trigger != "cron" or job.next_run is None:
            return None
        times, capped = self._missed_times(job, now)
        if len(times) < 2 and now - job.next_run <= CATCH_UP_GRACE_S:
            return None
        try:
            latest = _latest_at_or_before(job.schedule, now) if capped else times[-1]
        except (ValueError, OSError):
            latest = times[-1]
        latest = max(latest, times[-1])
        if now - latest > CATCH_UP_MAX_AGE_S:
            return _CatchUp(times, capped, latest, True, "missed_too_old")
        if job.catch_up == "skip":
            return _CatchUp(times, capped, latest, True, "catch_up_skip")
        return _CatchUp(times, capped, latest, False, "catch_up")

    def _note_missed(
        self, job: CronJob, plan: _CatchUp, now: float, reason: str, *, keep_latest: bool
    ) -> None:
        """A line per missed time (the most recent :data:`MAX_HISTORY_ROWS_PER_CATCH_UP`), then one
        line saying how many older ones are not listed.

        ``keep_latest`` leaves the latest time out: the catch-up run's own line stands for it.
        """
        times = plan.times[:-1] if keep_latest else list(plan.times)
        listed = times[-MAX_HISTORY_ROWS_PER_CATCH_UP:]
        hidden = len(times) - len(listed)
        more = "+" if plan.capped else ""
        if hidden > 0:
            self._note(
                job, now, "skipped", reason, scheduled_for=times[0],
                detail=f"{hidden}{more} earlier missed time(s) are not listed one by one",
            )
        for when in listed:
            self._note(job, now, "skipped", reason, scheduled_for=when)

    def _note_disabled(self, now: float) -> None:
        """Say once, per switched-off cron job, that its time came and nothing ran.

        A disabled job is not due, so nothing visited it: the history had no line for the most
        ordinary reason a schedule is quiet. Once per outage (:data:`_DISABLED_NOTED`), cleared by
        ``enable`` and ``disable``, and the job's ``next_run`` is left alone: ``enable`` computes a
        fresh one from the moment it is switched back on.
        """
        for job in self.store.list():
            if (
                job.enabled
                or job.trigger != "cron"
                or job.next_run is None
                or job.next_run > now
                or job.metadata.get(_DISABLED_NOTED)
            ):
                continue
            who = job.disabled_by or "human"
            self._note(
                job, now, "skipped", "disabled", scheduled_for=job.next_run,
                detail=f"switched off by {who}; later times are not listed until it is switched on",
            )
            job.metadata[_DISABLED_NOTED] = True
            self.store.add(job)

    def _claim(self, job: CronJob, now: float) -> bool:
        """Mark ``job`` as dispatching; False (and one history line) when it already is.

        In-process only: it is a dict on this ``Scheduler``, so it guards two ticks that share ONE
        scheduler object (a re-entrant call, two threads of the same process). It does not stop a
        second process on the same ``jobs.json`` from starting the same job; that is
        :class:`~chimera.scheduler.daemon.CronDaemon`'s per-home lock, which keeps a second daemon
        from ticking at all.
        """
        # `setdefault` is one atomic dict operation, which is what stands in for a lock here: this
        # module takes no threads of its own (a test holds it to that), and a check-then-add on a
        # set would let two ticks both pass the check.
        token = object()
        if self._running.setdefault(job.id, token) is token:
            return True
        key = (job.id, job.next_run)
        first = key not in self._noted_running
        self._noted_running.add(key)
        if first:
            self._note(job, now, "skipped", "already_running", scheduled_for=job.next_run)
        return False

    def _release(self, job: CronJob) -> None:
        self._running.pop(job.id, None)
        self._noted_running = {k for k in self._noted_running if k[0] != job.id}

    def _note_ran(
        self, job: CronJob, now: float, scheduled_for: float | None, plan: _CatchUp | None
    ) -> None:
        """The line for a dispatch that was attempted, read off how it ended."""
        status = job.last_status or "ok"
        if plan is not None:
            self._note_missed(job, plan, now, "catch_up_merged", keep_latest=True)
        if status == "budget":
            # Refused before it spent anything: the time did not run, and the money is why.
            self._note(
                job, now, "skipped", "budget_exceeded", status=status,
                scheduled_for=scheduled_for, detail=job.last_error or "",
            )
            return
        if status == "ok":
            reason = "catch_up" if plan is not None else "on_time"
            detail = ""
        else:
            reason = status
            detail = job.last_error or ""
        if plan is not None:
            more = "+" if plan.capped else ""
            lead = f"ran once for {len(plan.times)}{more} missed time(s)"
            detail = f"{lead}; {detail}" if detail else lead
        self._note(
            job, now, "ran", reason, status=status, scheduled_for=scheduled_for, detail=detail
        )

    def run_due(
        self,
        now: float,
        dispatch: Callable[[CronJob], DispatchStatus | None],
        *,
        job_timeout: float | None = None,
    ) -> list[CronJob]:
        """Dispatch every due cron job and advance its schedule. Returns those run.

        Dispatch is sequential, so without ``job_timeout`` one slow or hung job starves every other
        due job AND delays the next tick indefinitely: on a deployment running dozens of agent-jobs
        round the clock, a single stuck provider call silently stops the whole schedule. With it, a
        job that overruns is abandoned (Python cannot kill a running thread), logged, and its
        schedule is still advanced so the tick moves on instead of re-firing it immediately.

        **Catch-up.** A job whose time passed while nothing ran (the app was closed) is dispatched
        at most ONCE here, however many times it missed, and only if the latest missed time is no
        more than :data:`CATCH_UP_MAX_AGE_S` old and the job's ``catch_up`` is not ``skip``. Either
        way the history gets a line per missed time with the reason (``cron_history.jsonl``).
        """
        ran: list[CronJob] = []
        self._note_disabled(now)
        alive = self._scheduler_was_alive(now)
        for job in self.due(now):
            scheduled_for = job.next_run
            plan = self._plan_catch_up(job, now, alive)
            if plan is not None and plan.skip:
                _log.info(
                    "cron job %s (%s): %d missed time(s) not run (%s)",
                    job.name, job.id, len(plan.times), plan.reason,
                )
                self._note_missed(job, plan, now, plan.reason, keep_latest=False)
                # The schedule resumes at its next time. `last_run` stays what it was: nothing ran,
                # and stamping it would make `overdue()` and the screen read a skip as a run.
                job.next_run = _next_after(job.schedule, now, jitter_key=self._jitter_key(job))
                self.store.add(job)
                continue
            if not self._claim(job, now):
                continue
            _log.debug("dispatching cron job %s (%s)", job.name, job.id)
            # The outcome is recorded HERE, inside the guard, and `mark_ran` still runs outside it.
            # Those are two different facts and folding them into one field is what made a job that
            # has failed on every tick for a month look healthy: `last_run` is a minute ago, because
            # the attempt happened, and nothing anywhere said the attempt lost.
            try:
                veredito = _dispatch_bounded(job, dispatch, job_timeout)
                # `rejected` is not an error and reaches here without an exception: the job ran,
                # the work it produced failed the job's own verify command, and the workspace was
                # reverted. Recorded as its own outcome because "ok" for that is the state that
                # made a nightly job look healthy while producing nothing for a month.
                if veredito == "rejected":
                    self._record(
                        job, "rejected",
                        "the job ran and its verify command rejected the work, which was reverted",
                    )
                elif veredito == "cancelled":
                    # The operator stopped this dispatch (`cron kill`), so the job ran and was
                    # halted: not failed, not finished. Neither reading is true: counting it as
                    # a failure would let a kill ride `consecutive_failures` into the brake, and
                    # counting it as `ok` would zero the counter over real failures that came
                    # before. Neutral, like `budget`: the counter is left exactly as it was.
                    self._record(job, "cancelled", "stopped by the operator (cron kill)")
                else:
                    self._record(job, veredito or "ok", None)
            except TimeoutError:
                # Raised here, outside the dispatch, so the result sink never hears of it. The
                # owner is told by the daemon's failure notice, which reads `last_status` after
                # this tick, not from here: this module takes no clock and no I/O. Still open: the
                # abandoned thread may call the sink later and move `last_delivered_hash` on a job
                # this tick already saved, so that move can be lost.
                _log.warning(
                    "cron job %s (%s) exceeded %ss and was abandoned; the schedule continues",
                    job.name, job.id, job_timeout,
                )
                self._record(job, "timeout", f"exceeded {job_timeout}s and was abandoned")
            except BudgetExceeded as exc:
                # Not a failure: the job was refused because the money said no. Loud on purpose:
                # a refusal that only showed up as "nothing happened" is indistinguishable from a
                # stopped daemon, and this path runs jobs that watch real positions.
                _log.warning("cron job %s refused on budget: %s", job.name, exc)
                self._record(job, "budget", str(exc))
            except Exception as exc:  # a failing job must not break the scheduler
                _log.warning("cron job %s failed: %s", job.id, exc)
                self._record(job, "error", f"{type(exc).__name__}: {exc}")
            finally:
                self._release(job)
            # `mark_ran` already retires a one-shot job; it is also what stamps `last_run`.
            self.mark_ran(job, now)
            self._note_ran(job, now, scheduled_for, plan)
            self._brake(job)
            ran.append(job)
        # A caller with no clock of its own (a test, a one-shot) gets the start of the tick as its
        # end; the daemon replaces it with the real one through `note_tick_end`.
        self.note_tick_end(now)
        return ran

    def kill(self, job_id: str, *, now: float | None = None) -> bool:
        """Ask for one job's dispatch to stop, and say whether the request landed.

        Two cases, and the caller must be able to tell them apart:

        * the job is **enabled** — the flag is written and the running (or next) dispatch will
          consume it; the schedule is also pushed past ``now`` so a kill aimed at a job whose
          minute has not come does not fire it anyway;
        * the job is **disabled** — there is nothing running to stop, and writing a flag would
          be a lie the next ``enable`` would spend. Refused, loudly.

        The flag is a file beside ``jobs.json`` because the daemon and the CLI are different
        processes: the heartbeat already established that the scheduler's cross-process state is
        files in this folder, and a kill that only worked in-process would be a kill that never
        worked on a VPS.
        """
        job = self.store.get(job_id) if job_id in self.store else None
        if job is None or not job.enabled:
            return False
        # `store.path` is `<home>/scheduler/jobs.json`, so its grandparent is the home the flag
        # convention is defined against — the same folder the heartbeat and the job store live in.
        path = kill_flag_path(self.store.path.parent.parent, job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("kill", encoding="utf-8")
        if now is not None:
            job.next_run = now + 1.0
            self.store.add(job)
        return True

    @staticmethod
    def _record(job: CronJob, status: DispatchStatus, error: str | None) -> None:
        """Set the outcome on the job. `mark_ran` persists it a moment later, in the same tick."""
        job.last_status = status
        job.last_error = (error or "")[:300] or None
        # A budget refusal does not count as a failure: the job never ran, so nothing about it
        # failed, and letting it climb this counter would make a spending decision look like forty
        # broken dispatches to anyone reading `failing()`.
        # A kill is neutral in the other direction: the job RAN and was stopped by a person, so it
        # is not evidence of breakage (no increment) and not evidence of health (no reset).
        if status in ("ok", "budget"):
            job.consecutive_failures = 0
        elif status == "cancelled":
            pass
        else:
            job.consecutive_failures += 1

    def fire_event(self, event: str, now: float, dispatch: Callable[[CronJob], DispatchStatus | None]) -> list[CronJob]:
        """Dispatch every job registered for ``event``. Returns those run.

        Records the outcome, exactly as the cron path does. It used to swallow it: a bare
        ``try/except`` around the dispatch and a log line, with no ``_record``. So an event job's
        failures never touched ``last_status``, ``last_error`` or ``consecutive_failures`` — it
        reported as healthy forever, which is the state the cron path's own comment says made a job
        that had failed every tick for a month look fine, and the failure brake reads that counter.
        """
        ran: list[CronJob] = []
        for job in self.jobs_for_event(event):
            try:
                veredito = dispatch(job)
                if veredito == "rejected":
                    self._record(
                        job, "rejected",
                        "the job ran and its verify command rejected the work, which was reverted",
                    )
                else:
                    self._record(job, veredito or "ok", None)
            except BudgetExceeded as exc:
                _log.warning("event job %s refused on budget: %s", job.name, exc)
                self._record(job, "budget", str(exc))
            except Exception as exc:  # a failing job must not break the dispatcher
                _log.warning("event job %s failed: %s", job.id, exc)
                self._record(job, "error", f"{type(exc).__name__}: {exc}")
            self.mark_ran(job, now)
            self._note_ran(job, now, None, None)
            self._brake(job)
            ran.append(job)
        return ran
