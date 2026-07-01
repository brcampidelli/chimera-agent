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

from chimera.scheduler.models import CreatedBy, CronJob
from chimera.scheduler.store import CronStore
from chimera.telemetry import get_logger

_log = get_logger("scheduler.engine")


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

    def run_due(self, now: float, dispatch: Callable[[CronJob], None]) -> list[CronJob]:
        """Dispatch every due cron job and advance its schedule. Returns those run."""
        ran: list[CronJob] = []
        for job in self.due(now):
            _log.debug("dispatching cron job %s (%s)", job.name, job.id)
            try:
                dispatch(job)
            except Exception as exc:  # a failing job must not break the scheduler
                _log.warning("cron job %s failed: %s", job.id, exc)
            self.mark_ran(job, now)
            ran.append(job)
        return ran

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
