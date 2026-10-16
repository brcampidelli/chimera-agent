"""A random, persistent identifier for this installation's diagnostic reports.

The id is deliberately local to the app data home: deriving it from a machine, account, or device
would turn a support label into a fingerprint. Exclusive creation makes simultaneous first reports
converge on the one file rather than overwrite one another; a malformed file is replaced only via
an atomic sibling rename so a partial write cannot become an id.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import string
import tempfile
from pathlib import Path

_ID_LENGTH = 32
_ALPHABET = string.ascii_lowercase + string.digits
_ID_FILE = ".installation-id"


def _new_id() -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(_ID_LENGTH))


def _valid(value: str) -> bool:
    return len(value) == _ID_LENGTH and all(char in _ALPHABET for char in value)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return ""


def _replace(home: Path, path: Path, value: str) -> None:
    """Write ``value`` beside ``path`` and rename it over: no reader ever sees half an id."""
    fd, name = tempfile.mkstemp(dir=home, prefix=_ID_FILE + ".", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink()


def installation_id(home: Path) -> str:
    """Read or create the random id in this install's fixed backend home.

    Never raises: a home that cannot be written still gets an id for this report — one that will
    not survive the process, which is the truth about an install that cannot keep anything.
    """
    home = Path(home).resolve()
    path = home / _ID_FILE
    existing = _read(path)
    if _valid(existing):
        return existing
    value = _new_id()
    try:
        home.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        # Another first report won the race — use its id — or the file is malformed: replace it.
        existing = _read(path)
        if _valid(existing):
            return existing
        with contextlib.suppress(OSError):
            _replace(home, path, value)
        return value
    except OSError:
        return value
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(value + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        # Half-written is worse than absent: a truncated id would read as malformed and be
        # replaced next time, changing an id that was already handed out in this report.
        with contextlib.suppress(OSError):
            path.unlink()
    return value
