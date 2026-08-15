"""The two primitives the small JSON files Chimera keeps beside itself have to share.

Several stores here follow the same shape: read the whole file into memory, mutate the structure,
write it all back. Done naively that has two failure modes, and this module holds one answer for
each so they cannot be solved in one store and forgotten in the next — which is exactly what had
happened. :func:`atomic_write_text` covers the torn write; :func:`locked` covers the lost update.

**The torn write.** ``path.write_text(...)`` truncates first and fills after. A kill or a full disk
in between leaves a file that is neither the old contents nor the new, and for a whole-file JSON
store that means every record is gone, not the last one. Writing to a fresh temp file in the same
directory and renaming it over the target makes the switch a single atomic step.

**The lost update.** Two writers each read the file, each mutate their own copy, each write it all
back; the second one publishes a snapshot taken before the first one's change. Nothing raises,
nothing logs, and the missing record looks like it was never added.

Two writers is the normal case, not an exotic one. ``chimera serve`` runs the cron daemon in a
background thread of the gateway process, and any ``chimera memory add`` typed over SSH while that
is up is a second process against the same file.

**Advisory, and only that.** It coordinates writers that both take the lock. A process that ignores
it is not stopped, and nothing here protects a reader from seeing an older version — that job
belongs to the atomic replace the stores already do.

The lock lives in a **sibling** ``.lock`` file rather than the data file itself, because the data
file gets replaced out from under any descriptor held on it. Locking the thing you are about to
rename is locking a file nobody will be looking at a moment later.

Degrading: if the lock cannot be created or taken, the body runs **unlocked** with a warning. A
memory that fails to save is a worse outcome than a rare lost update, and with the atomic replace
the failure mode without a lock is a lost record rather than a corrupt file.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, TypeVar

from chimera.telemetry import get_logger

_log = get_logger("core.filelock")
_T = TypeVar("_T")

#: How long to keep retrying a rename or read that lost a race with another process, and the pause
#: between attempts. Both are tiny because the window being retried is a single syscall — this is
#: absorbing a collision, not waiting for anything.
RETRY_ATTEMPTS = 20
RETRY_PAUSE = 0.01


def retrying(action: Callable[[], _T]) -> _T:
    """Run ``action``, retrying briefly while the OS says the file is busy.

    POSIX renames a file over an open one without complaint; Windows refuses while any handle is
    open, so a reader that happens to be mid-read makes an unrelated writer's ``os.replace`` raise
    ``PermissionError``. Readers in these stores deliberately take no lock, which leaves exactly
    that window — small, and reached only under two processes, but reached, and observed.

    Retrying is the right shape rather than locking the readers: recall waiting on a writer would
    be a real cost for a collision measured in microseconds. After the last attempt the error is
    raised unchanged, because a permission problem that outlives the window is not a race.
    """
    for _ in range(RETRY_ATTEMPTS - 1):
        try:
            return action()
        except PermissionError:  # pragma: no cover - Windows-only timing
            time.sleep(RETRY_PAUSE)
    return action()


def atomic_write_text(path: Path, text: str) -> None:
    """Replace ``path``'s contents in one step, or leave the previous contents untouched.

    The temp file is unique per call rather than a fixed ``<name>.tmp``. A shared name means two
    writers that both got past the lock — the degraded path, or a process that never took it —
    interleave inside the *same* temp file, and the rename then publishes a mixture of two
    serialisations rather than either one of them. Same directory, because ``os.replace`` is only
    atomic within a filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            fh.write(text)
        retrying(lambda: os.replace(tmp_name, path))
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def read_text(path: Path) -> str:
    """Read a file that another process may be replacing under us. Empty string when absent."""
    if not path.exists():
        return ""
    return retrying(lambda: path.read_text(encoding="utf-8"))

if sys.platform == "win32":  # pragma: no cover - platform split, both halves ship
    import msvcrt

    def _acquire(handle: IO[Any]) -> None:
        # Locks one byte at offset 0. LK_LOCK retries for ~10s and then raises, which is the right
        # direction: a writer that cannot get in should say so rather than hang a 24/7 process.
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _release(handle: IO[Any]) -> None:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:  # the handle is about to close anyway
            _log.debug("unlock failed for %s", handle)

else:  # pragma: no cover - platform split, both halves ship
    import fcntl

    def _acquire(handle: IO[Any]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _release(handle: IO[Any]) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked(path: Path) -> Iterator[bool]:
    """Hold an exclusive advisory lock for ``path`` while the body runs.

    Yields whether a real lock was taken, so a caller that wants to record degraded operation can.
    Both flavours release on process death, so a writer killed mid-update does not wedge the file
    for everyone else — which is why this uses OS locks rather than an exclusive-create lockfile.
    """
    lock_path = Path(str(path) + ".lock")
    handle: IO[Any]
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")  # noqa: SIM115 - closed in the finally below
    except OSError as exc:
        # Read-only directory, exhausted descriptors, a path that is not writable. None of these
        # should stop the write itself.
        _log.warning("could not open lock for %s (%s); writing unlocked", path.name, exc)
        yield False
        return

    try:
        try:
            handle.write(b"\0")  # msvcrt needs a byte at offset 0 to lock
            handle.flush()
            os.lseek(handle.fileno(), 0, os.SEEK_SET)
            _acquire(handle)
        except OSError as exc:
            _log.warning("could not lock %s (%s); writing unlocked", path.name, exc)
            yield False
            return
        try:
            yield True
        finally:
            _release(handle)
    finally:
        handle.close()
