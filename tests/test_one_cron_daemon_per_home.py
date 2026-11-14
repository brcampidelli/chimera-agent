"""One cron daemon per home (P0-12, a14-03).

`Scheduler._claim` only guards ONE process, and the desktop app turns the cron on by default while
`chimera serve --cron` starts another over the same `jobs.json`. Two loops each see every due job,
and each dispatches it. The race was read in the code, not reproduced; these tests reproduce it
(the ``nolock`` arm) and then show the per-home OS lock closes it (the ``lock`` arm), across real
processes. The lock is an OS lock, so a killed daemon must not wedge the next one — that is tested
by killing one.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from chimera.scheduler import CronDaemon, CronStore, Scheduler
from chimera.scheduler.daemon import DaemonAlreadyRunning

REPO = Path(__file__).resolve().parents[1]
REPS = 20

# One worker process serves every repetition (a fresh interpreter per repetition would be ~1 s of
# imports each). Per repetition: announce ready, wait for the parent's `go`, then behave exactly as
# a second `serve` would: build a Scheduler on the shared jobs.json, try to start the daemon, run
# until the work is done. The dispatch sleeps so that, without the lock, both processes are inside
# it at once - which is what a real agent job (seconds long) gives the race for free.
#
# The loop ends on PROGRESS, not on a fixed wall-clock window. It used to stop 0.7 s after `go`, with
# the job due at 0.2 s; on a loaded machine a worker was frozen for longer than that (measured: ticks
# at 0.00-0.14 s, then nothing until the timer fired), so the winner held the lock, never saw the job
# come due, and the count read 0 - a failure of the test's clock, not of the lock. Now the loop runs
# until SETTLE seconds after its own dispatch finished (long enough for a rival with no lock to have
# dispatched too), with a ceiling that only matters when nothing was dispatched at all, and that
# case still reads 0 and fails: the assertion is not loosened.
_WORKER = textwrap.dedent(
    """
    import sys, threading, time
    from pathlib import Path
    from chimera.scheduler import CronDaemon, CronStore, Scheduler

    root, ident, reps, mode = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
    SETTLE, CEILING = float(sys.argv[5]), float(sys.argv[6])
    for rep in range(reps):
        sync = root / f"rep{rep}"
        (sync / f"ready_{ident}").write_text("1")
        while not (sync / "go").exists():
            time.sleep(0.002)

        stop = threading.Event()

        def dispatch(job, _sync=sync, _stop=stop):
            with open(_sync / "dispatches.log", "a") as fh:
                fh.write(ident + " ")  # split() on whitespace counts the entries
            time.sleep(0.25)
            threading.Timer(SETTLE, _stop.set).start()  # ticks keep going: a rival gets its chance

        daemon = CronDaemon(
            Scheduler(CronStore(sync / "scheduler" / "jobs.json")), dispatch, tick_seconds=0.02
        )
        if mode == "nolock":
            daemon.claim = lambda: True  # the behaviour before P0-12
        threading.Timer(CEILING, stop.set).start()
        daemon.run_forever(stop=stop)
        (sync / f"done_{ident}").write_text("1")
    """
)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _wait(paths: list[Path], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not all(p.exists() for p in paths):
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {[p.name for p in paths if not p.exists()]}")
        time.sleep(0.002)


def _dispatches_per_rep(tmp_path: Path, mode: str) -> list[int]:
    for rep in range(REPS):
        (tmp_path / f"rep{rep}").mkdir()
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", _WORKER, str(tmp_path), ident, str(REPS), mode, "0.3", "5"],
            env=_env(), cwd=REPO, stderr=subprocess.PIPE,
        )
        for ident in ("a", "b")
    ]
    counts: list[int] = []
    try:
        for rep in range(REPS):
            sync = tmp_path / f"rep{rep}"
            _wait([sync / "ready_a", sync / "ready_b"])
            now = time.time()
            store = CronStore(sync / "scheduler" / "jobs.json")
            Scheduler(store).schedule_once("job", now + 0.2, "x", now=now)
            (sync / "go").write_text("1")
            _wait([sync / "done_a", sync / "done_b"])
            log = sync / "dispatches.log"
            counts.append(len(log.read_text().split()) if log.exists() else 0)
    finally:
        for proc in procs:
            proc.kill()
            proc.communicate()
    return counts


def test_the_race_is_real_without_the_lock(tmp_path: Path) -> None:
    """The control: with the lock off the job runs twice, so the next test can fail."""
    counts = _dispatches_per_rep(tmp_path, "nolock")

    assert sum(1 for c in counts if c == 2) >= REPS // 2, counts


def test_two_processes_dispatch_a_job_exactly_once(tmp_path: Path) -> None:
    counts = _dispatches_per_rep(tmp_path, "lock")

    assert counts == [1] * REPS


# --- the lock is the OS's, so it dies with the process ------------------------------------------


def _daemon(home: Path, *, tick: float = 30.0) -> CronDaemon:
    return CronDaemon(
        Scheduler(CronStore(home / "scheduler" / "jobs.json")), lambda _j: None, tick_seconds=tick
    )


def test_start_refuses_while_another_daemon_holds_the_home(tmp_path: Path) -> None:
    first, second = _daemon(tmp_path), _daemon(tmp_path)
    assert first.claim()
    try:
        with pytest.raises(DaemonAlreadyRunning):
            second.start()
    finally:
        first.release()


def test_stopping_the_daemon_gives_the_lock_back(tmp_path: Path) -> None:
    # A short tick: the loop only sees `stop` after its sleep, and the default 30 s tick outlasted
    # the join, so the lock was (correctly) still held when the claim below ran.
    first = _daemon(tmp_path, tick=0.02)
    thread, stop = first.start()
    assert not _daemon(tmp_path).claim()

    stop.set()
    thread.join(timeout=5)
    assert not thread.is_alive()

    other = _daemon(tmp_path)
    assert other.claim()
    other.release()


def test_a_killed_daemon_does_not_wedge_the_home(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys, time\n"
            "from pathlib import Path\n"
            "from chimera.scheduler import CronDaemon, CronStore, Scheduler\n"
            "d = CronDaemon(Scheduler(CronStore(Path(sys.argv[1]) / 'scheduler' / 'jobs.json')), lambda j: None)\n"
            "assert d.claim()\n"
            "print('held', flush=True)\n"
            "time.sleep(60)\n",
            str(tmp_path),
        ],
        env=_env(), cwd=REPO, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert not _daemon(tmp_path).claim()  # while it lives, nobody else gets in
        holder.kill()  # no release(), no atexit: the crash case
        holder.wait(timeout=10)
        survivor = _daemon(tmp_path)
        # Windows documents the release of a dead process's byte-range lock as "unspecified" timing
        # (in practice milliseconds), so poll briefly instead of asserting on the same instant.
        deadline = time.monotonic() + 5
        got = survivor.claim()
        while not got and time.monotonic() < deadline:
            time.sleep(0.05)
            got = survivor.claim()
        assert got  # an exclusive-create sentinel file would still be there forever
        survivor.release()
    finally:
        holder.kill()
        holder.communicate()


def test_a_lock_that_cannot_be_opened_stops_the_daemon_instead_of_letting_two_start(
    tmp_path: Path,
) -> None:
    """A directory where the lock file should be made `try_hold` degrade to "no lock", and the
    daemon took that for success: both of two daemons came up. The daemon exists to be the only
    one, so it fails closed and says why."""
    from chimera.scheduler.daemon import start_unless_running

    (tmp_path / "scheduler" / "daemon.lock").mkdir(parents=True)
    first, second = _daemon(tmp_path, tick=0.02), _daemon(tmp_path, tick=0.02)

    assert not first.claim() and not second.claim()
    with pytest.raises(DaemonAlreadyRunning, match="unavailable"):
        first.start()
    said: list[str] = []
    assert start_unless_running(second, said.append) is None
    assert said and "NOT started" in said[0] and "unavailable" in said[0]
    stop = __import__("threading").Event()
    third = _daemon(tmp_path, tick=0.02)
    third.run_forever(stop=stop)  # returns at once: no lock, no ticks
