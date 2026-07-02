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
) -> Dispatch:
    """Build a dispatch that runs a job's ``action`` through ``run_task`` (task -> answer).

    ``on_result`` (optional) receives ``(job, answer)`` — e.g. to deliver the result to a
    chat platform. Generic and side-effect-light so it's easy to test and to wire.
    """

    def dispatch(job: CronJob) -> None:
        answer = run_task(job.action)
        _log.info("cron '%s' ran -> %s", job.name, (answer or "").replace("\n", " ")[:200])
        if on_result is not None:
            on_result(job, answer)

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
    ) -> None:
        self.scheduler = scheduler
        self.dispatch = dispatch
        self.tick_seconds = tick_seconds
        self._clock = clock
        self._sleep = sleep

    def tick(self, now: float | None = None) -> list[CronJob]:
        """One scheduler tick: dispatch every job due at ``now`` (defaults to the real clock)."""
        return self.scheduler.run_due(self._clock() if now is None else now, self.dispatch)

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
