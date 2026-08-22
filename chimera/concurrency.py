"""Deadlines you can actually walk away from.

Python cannot kill a running thread. So every deadline in this codebase is really the same promise:
*if this call overruns, stop waiting for it and carry on* — the caller gets a timeout, the work is
abandoned, and the program keeps moving.

Three places made that promise independently (`run_isolated`, `run_in_processes`, the scheduler's
bounded dispatch) and all three broke it in the same way, because the obvious implementation looks
correct and is not::

    pool = ThreadPoolExecutor(max_workers=1)
    try:
        pool.submit(fn).result(timeout=deadline)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)   # "abandoned"

It is not abandoned. `ThreadPoolExecutor` threads are non-daemon by design, and **both**
``concurrent.futures.thread._python_exit`` and ``threading._shutdown`` join every one of them at
interpreter exit. The call returns on time, the timeout is reported correctly, everything looks
right — and then the process hangs in ``Thread.join`` until the abandoned work finishes on its own.
For a job that sleeps ten minutes, that is a ten-minute freeze *after* the run reported success.

That failure is nastier than the one the deadline fixed, because it does not look like a hang in the
code that caused it. It looks like the CLI, or the test suite, or CI, locking up after finishing —
intermittently, with a blocked frame in ``threading.py`` and no visible connection to the caller.
Finding it took a stack dump.

``cancel_futures`` does not help: it only cancels work that has not STARTED, and a hung unit is by
definition already running. Nor does marking the thread daemon after the fact — three separate
private CPython structures decide whether a started thread is joined, and prying at all of them is a
fragile way to ask for something the standard library will hand you if you ask up front.

So: ask up front. Daemon threads, created here, with the reasoning in one place instead of three.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class Outcome(Generic[T]):
    """One call's result, filled in by its own thread. ``done`` unset means it overran."""

    name: str
    done: threading.Event = field(default_factory=threading.Event)
    value: T | None = None
    error: BaseException | None = None

    @property
    def timed_out(self) -> bool:
        return not self.done.is_set()


def call_with_deadline(fn: Callable[[], T], timeout: float | None) -> T:
    """Run ``fn()`` and raise ``TimeoutError`` if it overruns ``timeout``.

    ``None`` runs it inline — nothing pays for a thread when no deadline was asked for, and an
    inline call keeps the caller's stack, which matters when ``fn`` raises.

    An overrunning call is abandoned, not cancelled: it keeps running to completion on its daemon
    thread and its result is discarded. That is the only thing Python allows, and it is why ``fn``
    should not be something whose *partial* effect is dangerous.
    """
    if timeout is None:
        return fn()
    outcome: Outcome[T] = Outcome("call")
    _spawn(outcome, fn)
    if not outcome.done.wait(timeout):
        raise TimeoutError(f"call overran {timeout}s and was abandoned")
    if outcome.error is not None:
        raise outcome.error
    return outcome.value  # type: ignore[return-value]


#: How often a cancelled wait looks up. Short enough that Stop feels immediate, long enough that a
#: batch of a handful of units costs nothing to poll. Only reached when a caller passes
#: ``cancelled`` — without one the wait is a single blocking call, exactly as before.
_CANCEL_POLL_S = 0.2

#: How long a cancelled unit is given to stop by ITSELF before it is abandoned.
#:
#: Cooperative stopping is the better path and must stay the primary one: a unit that reads the
#: flag between steps returns a real outcome, so the caller learns *why* it stopped and can report
#: it. Abandoning only ever says *that* it stopped. Without this grace, adding the backstop turned
#: every clean cancel into an abandonment — caught by a test written for an older defect, where a
#: worker that used to be reported as rejected started arriving as a failure instead.
#:
#: Two seconds because the cooperative check happens the moment the current step returns; a unit
#: already on its way out takes milliseconds, and one that is truly stuck is stuck inside a model
#: call that has up to ``CHIMERA_REQUEST_TIMEOUT`` to run. Anything longer is the hang again with a
#: smaller number on it.
_CANCEL_GRACE_S = 2.0


def run_all_with_deadline(
    units: list[tuple[str, Callable[[], T]]],
    *,
    max_workers: int,
    timeout: float | None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Outcome[T]]:
    """Run every unit concurrently and wait at most ``timeout`` for **the whole batch**.

    One deadline for the batch rather than one each: a per-unit timeout would let N slow units cost
    N × timeout, which is not what a caller asking for a deadline means.

    Concurrency is bounded by a semaphore rather than a work queue, so every unit gets a thread
    immediately and only ``max_workers`` of them run at once. Callers here submit batches sized by a
    human (a handful of parallel agents), and a thread parked on a semaphore costs a stack — a queue
    would buy nothing and need its own shutdown story.

    ``cancelled`` ends the WAIT, which is the only thing a cancel can end.

    Cooperative stop flags are read between units — before one starts, after its model call
    returns — so a unit stuck *inside* a model call never reads one, and this function went on
    waiting for an outcome that would not arrive. Measured: a crew whose three workers had all
    finished correct work sat at `done: false` for twenty-two minutes, and Stop answered
    `{"ok": true, "cancelled": true}` to a run it could not touch. The deadline underneath is
    ``CHIMERA_BATCH_TIMEOUT``, four hours, in a desktop app with somebody watching.

    An abandoned unit keeps running on its daemon thread and its result is discarded — the same
    contract :func:`call_with_deadline` documents, and the only thing Python allows. What matters
    downstream is that it has NO outcome: ``timed_out`` stays true, so a caller filtering on
    success (``run_isolated`` merges only units that succeeded) discards its work rather than
    landing half of it.
    """
    gate = threading.Semaphore(max(1, max_workers))
    outcomes: dict[str, Outcome[T]] = {name: Outcome(name) for name, _ in units}
    for name, fn in units:
        _spawn(outcomes[name], fn, gate=gate, label=name)

    deadline = None if timeout is None else time.monotonic() + timeout
    for outcome in outcomes.values():
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        if cancelled is None:
            outcome.done.wait(remaining)
            continue
        _wait_unless_cancelled(outcome, remaining, cancelled)
    return outcomes


def _wait_unless_cancelled(
    outcome: Outcome[T], remaining: float | None, cancelled: Callable[[], bool]
) -> None:
    """Wait for one outcome, abandoning it shortly after the caller's flag goes up.

    Shortly, not immediately — see :data:`_CANCEL_GRACE_S`. A unit that stops by itself returns a
    real outcome and can say why; abandoning is the backstop for the one that cannot hear.

    The flag is read before the first sleep as well as between them, so a Stop that arrived while
    the batch was being assembled does not buy a poll interval per unit.
    """
    end = None if remaining is None else time.monotonic() + remaining
    abandon_at: float | None = None
    while True:
        now = time.monotonic()
        if abandon_at is None and cancelled():
            abandon_at = now + _CANCEL_GRACE_S
        limits = [x for x in (end, abandon_at) if x is not None]
        if limits:
            left = min(limits) - now
            if left <= 0:
                return
            if outcome.done.wait(min(_CANCEL_POLL_S, left)):
                return
        elif outcome.done.wait(_CANCEL_POLL_S):
            return


def _spawn(
    outcome: Outcome[T],
    fn: Callable[[], T],
    *,
    gate: threading.Semaphore | None = None,
    label: str = "call",
) -> None:
    """Start ``fn`` on a daemon thread that records into ``outcome`` and never raises."""

    def work() -> None:
        if gate is not None:
            gate.acquire()
        try:
            outcome.value = fn()
        except Exception as exc:  # noqa: BLE001 — a crashing unit is a failed unit, not a crash
            outcome.error = exc
        finally:
            if gate is not None:
                gate.release()
            outcome.done.set()

    threading.Thread(target=work, daemon=True, name=f"chimera-{label}").start()
