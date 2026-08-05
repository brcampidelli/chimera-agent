"""Deadlines that can actually be walked away from.

Three places in this codebase promised "an overrunning call is abandoned" and all three broke the
promise identically, because the obvious implementation — a `ThreadPoolExecutor` shut down with
`wait=False` — returns on time and then holds the interpreter open at exit until the abandoned work
finishes. The tests here pin the property that was missing, which is not "the call returns on time"
(that always worked) but "nothing is left that the process must wait for".

Every test is bounded by construction: none of them waits on a hung unit, because a test that proves
"the process can exit" by waiting for it to exit IS the hang.
"""

from __future__ import annotations

import threading
import time

import pytest

from chimera.concurrency import call_with_deadline, run_all_with_deadline


def _hang() -> int:
    time.sleep(600)
    return 0


def test_a_call_within_its_deadline_returns_its_value() -> None:
    assert call_with_deadline(lambda: 42, 5.0) == 42


def test_no_deadline_runs_inline_and_keeps_the_callers_stack() -> None:
    """`None` must not pay for a thread — and an inline call is also what preserves the traceback
    when the callable raises, which is the difference between a useful log line and a useless one."""
    here = threading.current_thread()
    assert call_with_deadline(lambda: threading.current_thread(), None) is here


def test_an_exception_propagates_rather_than_becoming_a_timeout() -> None:
    def boom() -> int:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        call_with_deadline(boom, 5.0)


def test_an_overrun_raises_and_leaves_only_a_daemon_thread() -> None:
    before = set(threading.enumerate())
    with pytest.raises(TimeoutError):
        call_with_deadline(_hang, 0.2)

    leftover = [t for t in threading.enumerate() if t not in before and t.is_alive()]
    assert leftover, "the abandoned work should still be running — otherwise this proves nothing"
    assert all(t.daemon for t in leftover), (
        "an abandoned call left a non-daemon thread: the process will hang at exit, long after "
        "the timeout was reported and everything looked fine"
    )


def test_a_batch_returns_as_soon_as_the_deadline_passes() -> None:
    started = time.monotonic()
    outcomes = run_all_with_deadline(
        [("fast", lambda: 1), ("hung", _hang)], max_workers=4, timeout=0.5
    )
    elapsed = time.monotonic() - started

    assert outcomes["fast"].value == 1 and not outcomes["fast"].timed_out
    assert outcomes["hung"].timed_out
    assert elapsed < 30, "one hung unit delayed the whole batch — the deadline bounded nothing"


def test_the_deadline_is_for_the_batch_not_for_each_unit() -> None:
    """Per-unit deadlines would let N slow units cost N × timeout, which is not what a caller asking
    for a deadline means."""
    started = time.monotonic()
    run_all_with_deadline([(f"h{i}", _hang) for i in range(4)], max_workers=4, timeout=0.5)
    assert time.monotonic() - started < 30


def test_max_workers_bounds_how_many_run_at_once() -> None:
    peak = 0
    live = 0
    lock = threading.Lock()

    def unit() -> int:
        nonlocal peak, live
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return 1

    run_all_with_deadline([(str(i), unit) for i in range(8)], max_workers=2, timeout=10.0)
    assert peak <= 2, f"{peak} units ran concurrently under max_workers=2"


def test_a_crashing_unit_is_a_failed_unit_not_a_failed_batch() -> None:
    def boom() -> int:
        raise RuntimeError("unit died")

    outcomes = run_all_with_deadline(
        [("ok", lambda: 7), ("bad", boom)], max_workers=2, timeout=10.0
    )
    assert outcomes["ok"].value == 7
    assert isinstance(outcomes["bad"].error, RuntimeError)
    assert not outcomes["bad"].timed_out  # it finished — badly, but it finished
