"""Cancelling has to end the WAIT, not just set a flag nobody reads.

Measured on rc13. A crew of three ran, all three produced correct work in their own worktrees, and
one of them reported. The run then sat at five frames for twenty-two minutes: `done: false`, no
worker process left alive, the backend answering health in two milliseconds. Pressing Stop replied
`{"ok": true, "cancelled": true}` and changed nothing for another seven and a half.

The flag was never the problem. `should_stop` is read BETWEEN units — before a worker starts and
after its model turn returns — so a worker stuck inside that turn never reads it, and
`run_all_with_deadline` goes on waiting for an outcome that will not arrive. The default it waits
under is `CHIMERA_BATCH_TIMEOUT`, four hours, in a desktop app with somebody watching.

What must NOT change is the safety property. An abandoned unit has no outcome, `succeeded()` is
False, and its worktree is discarded rather than merged — cancelling still cannot land half an
edit. That is asserted here, not assumed, because "make cancel faster" is exactly the change that
would trade it away.
"""

from __future__ import annotations

import threading
import time

from chimera.concurrency import run_all_with_deadline


def _hangs(started: threading.Event) -> int:
    started.set()
    time.sleep(600)
    return 0


def test_a_cancel_ends_the_wait_long_before_the_deadline() -> None:
    stop = threading.Event()
    started = threading.Event()
    cancel_after_start = threading.Thread(
        target=lambda: (started.wait(5), stop.set()), daemon=True
    )
    cancel_after_start.start()

    began = time.monotonic()
    outcomes = run_all_with_deadline(
        [("stuck", lambda: _hangs(started))],
        max_workers=2,
        timeout=14_400.0,  # the real default: four hours
        cancelled=stop.is_set,
    )
    elapsed = time.monotonic() - began

    assert elapsed < 10, f"waited {elapsed:.1f}s for a cancelled unit"
    assert outcomes["stuck"].timed_out, "an abandoned unit must not look finished"
    assert outcomes["stuck"].value is None


def test_a_cancelled_batch_still_reports_the_units_that_did_finish() -> None:
    # The whole point of stopping rather than killing: work already paid for is kept. A cancel that
    # discarded the finished workers would spend the money and throw away the result.
    stop = threading.Event()
    started = threading.Event()

    def quick() -> int:
        return 7

    threading.Thread(target=lambda: (started.wait(5), stop.set()), daemon=True).start()

    outcomes = run_all_with_deadline(
        [("done", quick), ("stuck", lambda: _hangs(started))],
        max_workers=2,
        timeout=14_400.0,
        cancelled=stop.is_set,
    )

    assert outcomes["done"].value == 7
    assert not outcomes["done"].timed_out
    assert outcomes["stuck"].timed_out


def test_without_a_cancel_signal_nothing_about_waiting_changes() -> None:
    # The parameter is optional and every existing caller omits it. If its absence changed the
    # timing at all, this fix would be a behaviour change wearing a bug fix's clothes.
    began = time.monotonic()
    outcomes = run_all_with_deadline(
        [("a", lambda: 1), ("b", lambda: 2)], max_workers=2, timeout=30.0
    )
    elapsed = time.monotonic() - began

    assert [outcomes["a"].value, outcomes["b"].value] == [1, 2]
    assert elapsed < 5


def test_a_cancel_already_set_before_the_call_returns_at_once() -> None:
    # The Stop that arrives while the batch is being assembled. Waiting the full deadline for units
    # that were cancelled before they began would be the same hang by a different door.
    began = time.monotonic()
    outcomes = run_all_with_deadline(
        [("stuck", lambda: _hangs(threading.Event()))],
        max_workers=1,
        timeout=14_400.0,
        cancelled=lambda: True,
    )
    elapsed = time.monotonic() - began

    assert elapsed < 5
    assert outcomes["stuck"].timed_out


def test_a_unit_that_stops_cleanly_is_not_abandoned() -> None:
    """The grace exists so cooperative stopping stays the primary path.

    A unit that reads the flag itself returns a real outcome, so the caller learns WHY it stopped
    and can report it. Abandoning only ever says THAT it stopped. Without the grace, adding the
    backstop turned every clean cancel into an abandonment — which an existing test caught: a crew
    worker that used to arrive as `rejected` started arriving as a `failure` instead.
    """
    stop = threading.Event()

    def notices_and_returns() -> str:
        while not stop.is_set():
            time.sleep(0.02)
        return "stopped on my own"

    threading.Thread(target=lambda: (time.sleep(0.3), stop.set()), daemon=True).start()

    outcomes = run_all_with_deadline(
        [("polite", notices_and_returns)],
        max_workers=1,
        timeout=14_400.0,
        cancelled=stop.is_set,
    )

    assert outcomes["polite"].value == "stopped on my own"
    assert not outcomes["polite"].timed_out
