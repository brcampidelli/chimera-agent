"""One Chimera Desktop server per data folder.

A server keeps in memory the turns it is running, the folder each one is editing, the undo offers
and the live frames of every conversation. Two servers on one ``CHIMERA_HOME`` each believe they are
alone: the lock that keeps two conversations from editing a folder at once holds only inside one
process, and Stop in one window cannot reach a turn the other runs. So the server claims the folder
before it builds anything.

The claim is an OS lock on ``<home>/desktop.lock``, taken without waiting. The OS releases it when
the process ends, however it ends, so a crash never leaves the folder locked. Beside it,
``desktop.url`` says where the holder is serving, so a second copy can point to the first instead
of only refusing.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import IO, Any

from chimera.telemetry import get_logger

_log = get_logger("core.instance")

LOCK_FILE = "desktop.lock"
URL_FILE = "desktop.url"

if sys.platform == "win32":  # pragma: no cover - platform split, both halves ship
    import msvcrt

    def _try_lock(handle: IO[Any]) -> bool:
        try:
            # LK_NBLCK fails at once when the byte is held; LK_LOCK would retry for ten seconds.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle: IO[Any]) -> None:
        with contextlib.suppress(OSError):
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

else:  # pragma: no cover - platform split, both halves ship
    import fcntl

    def _try_lock(handle: IO[Any]) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(handle: IO[Any]) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class HomeClaim:
    """This process's claim on one data folder. Held until :meth:`release` or the process ends."""

    def __init__(self, home: Path, handle: IO[Any]) -> None:
        self.home = home
        self._handle: IO[Any] | None = handle

    def announce(self, url: str) -> None:
        """Say where this server is, for a second copy that finds the folder taken."""
        (self.home / URL_FILE).write_text(url, encoding="utf-8")

    def release(self) -> None:
        if self._handle is None:
            return
        # The address first: once the lock is gone, another server may claim the folder and write
        # its own, and removing it after that would erase the new one.
        with contextlib.suppress(OSError):
            (self.home / URL_FILE).unlink(missing_ok=True)
        _unlock(self._handle)
        self._handle.close()
        self._handle = None


def claim_home(home: Path) -> HomeClaim | None:
    """Claim ``home`` for this process, or None when another server holds it."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    # Append mode, so opening never truncates a file another process holds; one byte to lock.
    handle = (home / LOCK_FILE).open("a+b")
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)
    if not _try_lock(handle):
        handle.close()
        return None
    return HomeClaim(home, handle)


def running_url(home: Path) -> str | None:
    """Where the server holding ``home`` says it is serving, if it has said."""
    try:
        text = (Path(home) / URL_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None
