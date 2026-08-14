"""Scheduling engine: due-job computation and event dispatch.

All time is passed in explicitly (``now`` as epoch seconds), so behaviour is fully
deterministic and testable — there is no hidden ``sleep`` or wall-clock read here.
A separate runner (CLI/daemon) supplies the real clock.
"""

from __future__ import annotations

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


def _dispatch_bounded(
    job: CronJob, dispatch: Callable[[CronJob], None], timeout: float | None
) -> None:
    """Run ``dispatch(job)``, raising :class:`TimeoutError` if it overruns ``timeout``.

    ``None`` runs it inline (the previous, unbounded behaviour) so nothing pays for a thread when
    no deadline is set. With a deadline the job runs on a daemon thread and an overrun is abandoned
    — a running job cannot be killed in Python, so waiting for it would reproduce the very stall the
    timeout exists to prevent.

    That abandonment used to be a lie: this ran on a ``ThreadPoolExecutor``, whose threads are joined
    at interpreter exit no matter what ``shutdown(wait=False)`` says, so a stuck job let the tick
    finish and then held the process open until it unstuck. See :mod:`chimera.concurrency`.
    """
    call_with_deadline(lambda: dispatch(job), timeout)


def _next_after(cron_expr: str, after_epoch: float) -> float:
    base = datetime.fromtimestamp(after_epoch, tz=UTC)
    return float(croniter(cron_expr, base).get_next(float))


class Scheduler:
    """Creates, lists and dispatches scheduled jobs over a :class:`CronStore`."""

    def __init__(self, store: CronStore) -> None:
        self.store = store

    def schedule_cron(
        self,
        name: str,
        cron_expr: str,
        action: str,
        *,
        now: float,
        created_by: CreatedBy = "human",
    ) -> CronJob:
        if not croniter.is_valid(cron_expr):
            raise ValueError(f"invalid cron expression: {cron_expr!r}")
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="cron",
            schedule=cron_expr,
            action=action,
            created_by=created_by,
            # Defend the "self-learned crons start disabled" invariant at the boundary, not just in
            # the learner: an agent-created cron must not fire until a human enables it.
            enabled=created_by != "agent",
            next_run=_next_after(cron_expr, now),
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
    ) -> CronJob:
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="event",
            schedule=event,
            action=action,
            created_by=created_by,
            enabled=created_by != "agent",  # agent-created triggers start disabled (same invariant)
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
    ) -> CronJob:
        """Register a job fired by an inbound HTTP POST to ``/webhook/<hook>``."""
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            trigger="webhook",
            schedule=hook,
            action=action,
            created_by=created_by,
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

    def fire_webhook(self, hook: str, now: float, dispatch: Callable[[CronJob], None]) -> list[CronJob]:
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
        """
        return [
            job
            for job in self.store.list()
            if job.enabled and job.consecutive_failures >= at_least
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
        if job.trigger == "cron":
            job.next_run = _next_after(job.schedule, now)
        self.store.add(job)
        return job

    def disable(self, job_id: str) -> CronJob:
        job = self.store.get(job_id)
        job.enabled = False
        self.store.add(job)
        return job

    def mark_ran(self, job: CronJob, now: float) -> None:
        job.last_run = now
        if job.trigger == "cron":
            job.next_run = _next_after(job.schedule, now)
        self.store.add(job)

    def run_due(
        self,
        now: float,
        dispatch: Callable[[CronJob], None],
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
                _dispatch_bounded(job, dispatch, job_timeout)
                self._record(job, "ok", None)
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

    def fire_event(self, event: str, now: float, dispatch: Callable[[CronJob], None]) -> list[CronJob]:
        """Dispatch every job registered for ``event``. Returns those run."""
        ran: list[CronJob] = []
        for job in self.jobs_for_event(event):
            try:
                dispatch(job)
            except Exception as exc:
                _log.warning("event job %s failed: %s", job.id, exc)
            self.mark_ran(job, now)
            ran.append(job)
        return ran
