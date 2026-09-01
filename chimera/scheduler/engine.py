"""Scheduling engine: due-job computation and event dispatch.

All time is passed in explicitly (``now`` as epoch seconds), so behaviour is fully
deterministic and testable — there is no hidden ``sleep`` or wall-clock read here.
A separate runner (CLI/daemon) supplies the real clock.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from croniter import croniter

from chimera.concurrency import call_with_deadline
from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler.models import CreatedBy, CronJob, DispatchStatus
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


def _next_after(cron_expr: str, after_epoch: float, *, jitter_key: str = "") -> float:
    """The next epoch matching ``cron_expr``, read in the machine's own time zone.

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
    base = datetime.fromtimestamp(after_epoch, tz=UTC).astimezone()
    ticker = croniter(cron_expr, base)
    nxt = float(ticker.get_next(float))
    if not jitter_key:
        return nxt
    # The interval is measured from the schedule itself rather than assumed, so `*/5` and `0 7 * * *`
    # each get an offset proportional to what they actually promise.
    period = float(ticker.get_next(float)) - nxt
    return nxt + _jitter(jitter_key, period)


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
        is — ``cron list``, ``cron doctor`` and ``/api/features`` all read this job.
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
    ) -> CronJob:
        """Register a job fired by a cron expression.

        verify: Shell command that decides whether this job's dispatch KEPT its work; empty means
            no gate, which is today's behaviour. Accepted here rather than only on the model
            because a field nothing can set is a field nobody has: `CronJob` has carried `verify`
            and `max_attempts` since the harness landed, and neither the HTTP route nor the CLI
            could write them, so the gate could never arm for any user.
        max_attempts: How many times one dispatch may try. Worth raising only alongside `verify` —
            without a gate nothing can tell a failed attempt from a finished one.
        """
        if not croniter.is_valid(cron_expr):
            raise ValueError(f"invalid cron expression: {cron_expr!r}")
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
        verify: str = "",
        max_attempts: int = 1,
    ) -> CronJob:
        """Register a job fired by a named event.

        verify: Shell command that decides whether this job's dispatch KEPT its work; empty means
            no gate, which is today's behaviour. Accepted here rather than only on the model
            because a field nothing can set is a field nobody has: `CronJob` has carried `verify`
            and `max_attempts` since the harness landed, and neither the HTTP route nor the CLI
            could write them, so the gate could never arm for any user.
        max_attempts: How many times one dispatch may try. Worth raising only alongside `verify` —
            without a gate nothing can tell a failed attempt from a finished one.
        """
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="event",
            schedule=event,
            action=action,
            created_by=created_by,
            enabled=created_by != "agent",  # agent-created triggers start disabled (same invariant)
            verify=verify,
            max_attempts=max(1, max_attempts),
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
        """
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="webhook",
            schedule=hook,
            action=action,
            created_by=created_by,
            verify=verify,
            max_attempts=max(1, max_attempts),
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

    def due(self, now: float) -> list[CronJob]:
        """Enabled cron jobs whose ``next_run`` is at or before ``now``."""
        return [
            job
            for job in self.store.list()
            if job.enabled
            and job.trigger == "cron"
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
        self.store.add(job)
        return job

    def mark_ran(self, job: CronJob, now: float) -> None:
        job.last_run = now
        if job.trigger == "cron":
            job.next_run = _next_after(job.schedule, now, jitter_key=self._jitter_key(job))
        self.store.add(job)

    def run_due(
        self,
        now: float,
        dispatch: Callable[[CronJob], DispatchStatus | None],
        *,
        job_timeout: float | None = None,
    ) -> list[CronJob]:
        """Dispatch every due cron job and advance its schedule. Returns those run.

        Dispatch is sequential, so without ``job_timeout`` one slow or hung job starves every other
        due job AND delays the next tick indefinitely — on a deployment running dozens of agent-jobs
        round the clock, a single stuck provider call silently stops the whole schedule. With it, a
        job that overruns is abandoned (Python cannot kill a running thread), logged, and its
        schedule is still advanced so the tick moves on instead of re-firing it immediately.
        """
        ran: list[CronJob] = []
        for job in self.due(now):
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
                else:
                    self._record(job, veredito or "ok", None)
            except TimeoutError:
                _log.warning(
                    "cron job %s (%s) exceeded %ss and was abandoned; the schedule continues",
                    job.name, job.id, job_timeout,
                )
                self._record(job, "timeout", f"exceeded {job_timeout}s and was abandoned")
            except BudgetExceeded as exc:
                # Not a failure: the job was refused because the money said no. Loud on purpose —
                # a refusal that only showed up as "nothing happened" is indistinguishable from a
                # stopped daemon, and this path runs jobs that watch real positions.
                _log.warning("cron job %s refused on budget: %s", job.name, exc)
                self._record(job, "budget", str(exc))
            except Exception as exc:  # a failing job must not break the scheduler
                _log.warning("cron job %s failed: %s", job.id, exc)
                self._record(job, "error", f"{type(exc).__name__}: {exc}")
            self.mark_ran(job, now)
            self._brake(job)
            ran.append(job)
        return ran

    @staticmethod
    def _record(job: CronJob, status: DispatchStatus, error: str | None) -> None:
        """Set the outcome on the job. `mark_ran` persists it a moment later, in the same tick."""
        job.last_status = status
        job.last_error = (error or "")[:300] or None
        # A budget refusal does not count as a failure: the job never ran, so nothing about it
        # failed, and letting it climb this counter would make a spending decision look like forty
        # broken dispatches to anyone reading `failing()`.
        if status in ("ok", "budget"):
            job.consecutive_failures = 0
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
            self._brake(job)
            ran.append(job)
        return ran
