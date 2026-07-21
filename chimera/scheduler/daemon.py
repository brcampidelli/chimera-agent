"""Cron daemon — the runner that gives stored crons a real clock.

The :class:`~chimera.scheduler.engine.Scheduler` is deterministic (``run_due`` takes ``now``
explicitly); this daemon supplies the wall clock, ticking on an interval and dispatching the
jobs that are due. It's what turns ``chimera serve`` from a purely reactive gateway into an
agent that also acts on a schedule. Clock and sleep are injected, so the loop is fully
unit-testable without real time, and a failing tick or job never kills the loop.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from chimera.scheduler.engine import Scheduler
from chimera.scheduler.models import CronJob
from chimera.telemetry import get_logger

_log = get_logger("scheduler.daemon")

Dispatch = Callable[[CronJob], None]


def make_agent_dispatch(
    run_task: Callable[[str], str],
    on_result: Callable[[CronJob, str], None] | None = None,
    *,
    delivery_retries: int = 2,
) -> Dispatch:
    """Build a dispatch that runs a job's ``action`` through ``run_task`` (task -> answer).

    ``on_result`` (optional) receives ``(job, answer)`` — e.g. to deliver the result to a
    chat platform or a durable sink. Delivery is *confirmed*: it is retried up to
    ``delivery_retries`` extra times and every attempt is logged, so a cron result is never
    silently lost the way a fire-and-forget log line would be. Generic and side-effect-light
    so it's easy to test and to wire.
    """

    def dispatch(job: CronJob) -> None:
        answer = run_task(job.action)
        _log.info("cron '%s' ran -> %s", job.name, (answer or "").replace("\n", " ")[:200])
        if on_result is None:
            return
        last_exc: Exception | None = None
        for attempt in range(1, delivery_retries + 2):
            try:
                on_result(job, answer)
                _log.info("cron '%s' result delivered (attempt %d)", job.name, attempt)
                return
            except Exception as exc:  # noqa: BLE001 — retry, then give up loudly
                last_exc = exc
                _log.warning("cron '%s' delivery attempt %d failed: %s", job.name, attempt, exc)
        _log.error(
            "cron '%s' delivery failed after %d attempt(s): %s",
            job.name,
            delivery_retries + 1,
            last_exc,
        )

    return dispatch


class CronDaemon:
    """Ticks a :class:`Scheduler` on the real clock and dispatches due jobs."""

    def __init__(
        self,
        scheduler: Scheduler,
        dispatch: Dispatch,
        *,
        tick_seconds: float = 30.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        job_timeout: float | None = 1800.0,
    ) -> None:
        self.scheduler = scheduler
        self.dispatch = dispatch
        self.tick_seconds = tick_seconds
        self._clock = clock
        self._sleep = sleep
        # Wall-clock ceiling per job. Dispatch is sequential, so an unbounded job starves every
        # other due job and stalls the next tick — on a 24/7 deployment one stuck provider call
        # silently stops the whole schedule. 30 min is generous for an agent job and still bounded;
        # None restores the old unbounded behaviour for a caller that truly wants it.
        self.job_timeout = job_timeout

    def tick(self, now: float | None = None) -> list[CronJob]:
        """One scheduler tick: dispatch every job due at ``now`` (defaults to the real clock).

        Reloads the job store first so crons added out-of-process (``chimera cron add`` in
        another shell/container) take effect without restarting the daemon.
        """
        try:
            self.scheduler.store.reload_if_changed()
        except Exception as exc:  # noqa: BLE001 — a bad reload must not skip the tick
            _log.warning("cron store reload failed: %s", exc)
        return self.scheduler.run_due(
            self._clock() if now is None else now, self.dispatch, job_timeout=self.job_timeout
        )

    def run_forever(self, *, stop: threading.Event | None = None) -> None:
        """Tick, sleep, repeat until ``stop`` is set. A bad tick is logged, never fatal."""
        _log.info("cron daemon started (tick=%ss)", self.tick_seconds)
        while stop is None or not stop.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 — the daemon must survive any tick failure
                _log.warning("cron tick failed: %s", exc)
            self._sleep(self.tick_seconds)

    def start(self) -> tuple[threading.Thread, threading.Event]:
        """Run the loop in a background daemon thread. Returns ``(thread, stop_event)``."""
        stop = threading.Event()
        thread = threading.Thread(
            target=self.run_forever, kwargs={"stop": stop}, daemon=True, name="chimera-cron"
        )
        thread.start()
        return thread, stop
