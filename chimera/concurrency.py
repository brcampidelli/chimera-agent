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


def run_all_with_deadline(
    units: list[tuple[str, Callable[[], T]]],
    *,
    max_workers: int,
    timeout: float | None,
) -> dict[str, Outcome[T]]:
    """Run every unit concurrently and wait at most ``timeout`` for **the whole batch**.

    One deadline for the batch rather than one each: a per-unit timeout would let N slow units cost
    N × timeout, which is not what a caller asking for a deadline means.

    Concurrency is bounded by a semaphore rather than a work queue, so every unit gets a thread
    immediately and only ``max_workers`` of them run at once. Callers here submit batches sized by a
    human (a handful of parallel agents), and a thread parked on a semaphore costs a stack — a queue
    would buy nothing and need its own shutdown story.
    """
    gate = threading.Semaphore(max(1, max_workers))
    outcomes: dict[str, Outcome[T]] = {name: Outcome(name) for name, _ in units}
    for name, fn in units:
        _spawn(outcomes[name], fn, gate=gate, label=name)

    deadline = None if timeout is None else time.monotonic() + timeout
    for outcome in outcomes.values():
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        outcome.done.wait(remaining)
    return outcomes


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
