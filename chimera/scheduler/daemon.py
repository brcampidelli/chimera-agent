"""Cron daemon — the runner that gives stored crons a real clock.

The :class:`~chimera.scheduler.engine.Scheduler` is deterministic (``run_due`` takes ``now``
explicitly); this daemon supplies the wall clock, ticking on an interval and dispatching the
jobs that are due. It's what turns ``chimera serve`` from a purely reactive gateway into an
agent that also acts on a schedule. Clock and sleep are injected, so the loop is fully
unit-testable without real time, and a failing tick or job never kills the loop.

**The heartbeat** (:meth:`CronDaemon.tick` → :func:`write_heartbeat`) is the daemon's one
outward sign of life, and it exists because of the gap `cron doctor` itself names: a crashed
process cannot log its own crash, so a dead daemon is invisible to every check that lives
inside it. The heartbeat is written to disk — not to a log, not to memory — so a *separate*
process (the host cron `docs/deploy.md` already recommends, or the app's next start) can read
it and notice the silence. That is the whole of issue #26's "own clock and own liveness": the
daemon does not watch itself; it leaves a timestamp where something else can find it stale.
"""

from __future__ import annotations

import inspect
import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from chimera.orchestration.budget import BudgetExceeded
from chimera.scheduler.engine import Scheduler
from chimera.scheduler.models import CronJob, DispatchStatus, JobOutcome
from chimera.telemetry import get_logger

_log = get_logger("scheduler.daemon")

#: Where the daemon leaves its sign of life, relative to ``CHIMERA_HOME``. Beside ``jobs.json``
#: (same directory), so one folder is the whole scheduler's state and a backup of one is a
#: backup of both.
HEARTBEAT_RELPATH = Path("scheduler") / "heartbeat.json"

Dispatch = Callable[[CronJob], "DispatchStatus | None"]
"""A dispatch may report how the job ended. ``None`` means "nothing to report", which the
engine reads as ``ok`` — every dispatch written before this returns None and keeps its
meaning, so the new outcome costs nothing to the callers that have no verdict to give."""


def write_heartbeat(path: Path, *, now: float, tick_seconds: float, pid: int) -> None:
    """Write the daemon's sign of life: one small JSON file, atomically replaced.

    Atomic (unique temp + ``os.replace``) for the same reason the job store is: a reader must
    never see a half-written heartbeat, because a torn file would read as "no heartbeat" and
    cry wolf about a daemon that is alive. The ``finally`` unlink keeps a failed write from
    leaving a temp file behind.

    Nothing here is clever on purpose. A reader answers one question — *did the daemon tick
    recently?* — from three fields: when it last ticked, how often it ticks, and which process
    was ticking. Everything a verdict needs beyond that (``now``) comes from the reader's own
    clock, which is the point: the writer being dead must not matter.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        tmp.write_text(
            json.dumps(
                {"at": now, "tick_seconds": tick_seconds, "pid": pid},
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def read_heartbeat(path: Path) -> dict[str, float | int] | None:
    """The last heartbeat, or ``None`` when there is none (or it cannot be read).

    ``None`` is "no signal", not "dead": a daemon that has never run and a corrupt file are
    both absence of evidence, and the caller decides what absence means — the same rule the
    engine's ``last_status=None`` already follows for a job that was never dispatched.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    at = raw.get("at")
    if not isinstance(at, (int, float)):
        return None
    return {
        "at": float(at),
        "tick_seconds": float(raw.get("tick_seconds") or 0.0),
        "pid": int(raw.get("pid") or 0),
    }


def heartbeat_age(path: Path, *, now: float) -> float | None:
    """Seconds since the last heartbeat, or ``None`` when there is none.

    The one number a watcher needs. Negative ages (a heartbeat dated in the future — a clock
    that moved backwards, a restored backup) are reported as 0.0 rather than passed through:
    "the daemon ticked -5s ago" is not a sentence, and a negative would silently pass every
    staleness comparison.
    """
    beat = read_heartbeat(path)
    if beat is None:
        return None
    return max(0.0, now - beat["at"])


def _takes_status(on_result: Callable[..., None]) -> bool:
    """Whether a result sink accepts ``status=``. A sink written before it existed is called the old
    way, with ``(job, answer)``, and keeps exactly the behaviour it had."""
    try:
        params = inspect.signature(on_result).parameters
    except (TypeError, ValueError):  # an unintrospectable callable: assume the old shape
        return False
    return "status" in params or any(p.kind is p.VAR_KEYWORD for p in params.values())


def make_agent_dispatch(
    run_task: Callable[[str], str],
    on_result: Callable[..., None] | None = None,
    *,
    delivery_retries: int = 2,
    run_job: Callable[[CronJob], JobOutcome | str] | None = None,
) -> Dispatch:
    """Build a dispatch that runs a job's ``action`` through ``run_task`` (task -> answer).

    ``run_job`` is the job-aware form and wins when given: a caller that has to honour the job's own
    settings — its spend cap, whether it is exempt from the daily one — needs the job, not just the
    action string. ``run_task`` stays for every caller that does not, which is most of them, and is
    what the tests drive.

    ``on_result`` (optional) receives ``(job, answer)`` — e.g. to deliver the result to a
    chat platform or a durable sink. Delivery is *confirmed*: it is retried up to
    ``delivery_retries`` extra times and every attempt is logged, so a cron result is never
    silently lost the way a fire-and-forget log line would be. Generic and side-effect-light
    so it's easy to test and to wire.

    A sink that accepts ``status=`` (as :func:`~chimera.scheduler.delivery.make_deliver` does) is
    also told how the dispatch went — ``ok``, ``rejected``, and, for a job whose ``notify`` is not
    ``always``, ``error`` or ``budget`` with the exception as the answer. That last part is what
    makes ``notify="failures_only"`` mean anything: an exception never reached the sink at all, so
    "only failures" would have been "only what a verify gate rejected". ``always`` keeps the old
    contract — a raised dispatch delivers nothing — because that is what it promises.
    """
    takes_status = on_result is not None and _takes_status(on_result)

    def _send(job: CronJob, answer: str, status: str) -> None:
        if on_result is None:
            return
        last_exc: Exception | None = None
        for attempt in range(1, delivery_retries + 2):
            try:
                if takes_status:
                    on_result(job, answer, status=status)
                else:
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

    def dispatch(job: CronJob) -> DispatchStatus | None:
        try:
            bruto = run_job(job) if run_job is not None else run_task(job.action)
        except Exception as exc:
            # Re-raised untouched: the engine records the failure and drives the brake from it.
            # This only tells the sink first, and only where the owner asked for failures.
            if takes_status and job.notify != "always":
                falha = "budget" if isinstance(exc, BudgetExceeded) else "error"
                _send(job, f"The scheduled run did not finish: {type(exc).__name__}: {exc}", falha)
            raise
        # A bare string is a caller with no verdict — a job that declared no gate has nothing
        # that could reject it — and is read as `ok`, which is what every caller meant before.
        outcome = bruto if isinstance(bruto, JobOutcome) else JobOutcome(str(bruto or ''))
        answer = outcome.answer
        if outcome.cancelled:
            # The operator stopped this run (`cron kill`). `JobOutcome.cancelled` said so and the
            # engine has a `cancelled` status waiting for it, and this line was the one that never
            # passed it on: the flag was dropped here, so a killed run was recorded `ok` — zeroing
            # the failure count over real failures before it — and its partial, unverified answer
            # was posted to the channel as though the job had finished.
            _log.info("cron '%s' was stopped by the operator; nothing is delivered", job.name)
            return "cancelled"
        status: DispatchStatus | None = None if outcome.ok else "rejected"
        _log.info(
            "cron '%s' ran%s -> %s",
            job.name,
            " (its gate rejected the work)" if status else "",
            (answer or "").replace('\\n', " ")[:200],
        )
        if on_result is None:
            return status
        _send(job, answer, status or "ok")
        return status

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
        heartbeat_path: Path | None = None,
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
        # Where the sign of life goes. Derived from the job store's own directory by default, so
        # every existing caller (serve, app, the tests) gets a heartbeat without a new argument —
        # and a caller that wants it elsewhere (a tmp_path in a test) passes one. ``None`` keeps
        # the old behaviour exactly: no file, no signal, and `cron doctor` reads absence as
        # "nothing to say about the daemon", which is what it said before this existed.
        self.heartbeat_path = (
            heartbeat_path
            if heartbeat_path is not None
            else scheduler.store.path.parent / "heartbeat.json"
        )

    def tick(self, now: float | None = None) -> list[CronJob]:
        """One scheduler tick: dispatch every job due at ``now`` (defaults to the real clock).

        Reloads the job store first so crons added out-of-process (``chimera cron add`` in
        another shell/container) take effect without restarting the daemon.

        The heartbeat is written AFTER the tick's work, not before: a heartbeat that says "alive"
        before the work ran would let a daemon that dies mid-tick leave a fresh-looking beat over
        a tick that never dispatched. The beat is the record of a completed tick.
        """
        try:
            self.scheduler.store.reload_if_changed()
        except Exception as exc:  # noqa: BLE001 — a bad reload must not skip the tick
            _log.warning("cron store reload failed: %s", exc)
        at = self._clock() if now is None else now
        ran = self.scheduler.run_due(at, self.dispatch, job_timeout=self.job_timeout)
        if self.heartbeat_path is not None:
            try:
                write_heartbeat(
                    self.heartbeat_path,
                    now=at,
                    tick_seconds=self.tick_seconds,
                    pid=os.getpid(),
                )
            except OSError as exc:  # noqa: BLE001 — a failed beat must not kill the tick
                _log.warning("cron heartbeat write failed: %s", exc)
        return ran

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
