"""Chimera's own files — its ``.env`` and its data folder — recognised by what they ARE, not by name.

Several doors keep a path away from these two: the desktop bridge (a client must not answer an
approval by writing ``<home>/approvals/<id>.answer.json``, nor read the keys in ``.env``), the
app's file routes, and the agent's own write tools. Each used to compare paths as strings or as
``Path`` objects, and on Windows one file has many spellings that ``Path.resolve`` does not unify:
``\\\\?\\C:\\…``, ``\\\\localhost\\C$\\…``, ``\\\\?\\UNC\\…``, a trailing dot or space
(``.env.``, ``.env ``), an alternate data stream (``.env::$DATA``), an 8.3 short name (``ENV~1``).
The adversarial review of 2026-10-04 wrote and read the real ``.env`` and answered an approval
through those spellings.

So the question is asked of the FILE SYSTEM: two paths are the same when ``os.stat`` gives the
same ``(st_dev, st_ino)`` — which a spelling cannot change — and a path is inside a folder when it,
or the nearest ancestor that exists, is that folder or below it by the same test. A target that does
not exist yet (the answer file a write would create) is judged by its existing ancestors.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: A path written as a device or network name: ``\\?\``, ``\\.\``, ``\\server\share``, ``//…``.
#: No client of the app's file surfaces needs one, and each is a spelling `Path` does not reduce to
#: the drive-letter form the checks compare against — so it is refused outright where untrusted
#: text names a path.
_DEVICE_OR_UNC = re.compile(r"^[\\/]{2}")


def is_device_or_unc(text: str) -> bool:
    """Whether ``text`` starts like ``\\\\?\\…``, ``\\\\.\\…``, ``\\\\server\\…`` or ``//…``."""
    return bool(_DEVICE_OR_UNC.match(str(text).strip()))


def normal_name(part: str) -> str:
    """One path component as Windows opens it: cut at ``:`` (a stream), trailing dots and spaces gone.

    ``.env::$DATA``, ``.env.`` and ``.env `` all open ``.env``; matching a credential file name
    against the raw text missed every one of them.
    """
    return part.split(":", 1)[0].rstrip(" .")


def _identity(path: Path) -> tuple[int, int] | None:
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    if not st.st_ino:  # a file system that reports none: identity says nothing here
        return None
    return st.st_dev, st.st_ino


def same_file(a: Path, b: Path) -> bool:
    """Whether ``a`` and ``b`` are one file or folder, however each is spelled."""
    ia, ib = _identity(a), _identity(b)
    if ia is not None and ib is not None:
        return ia == ib
    try:
        return Path(a).resolve() == Path(b).resolve()
    except (OSError, ValueError):
        return False


def _existing_chain(path: Path) -> list[Path]:
    """``path`` and its ancestors, nearest first, from the first one that exists."""
    p = Path(path)
    chain = [p, *p.parents]
    for i, candidate in enumerate(chain):
        if _identity(candidate) is not None:
            return chain[i:]
    return []


def within(target: Path, folder: Path) -> bool:
    """Whether ``target`` is ``folder`` or anything under it, by file identity (and by path).

    By path too, because a target whose every ancestor is missing has no identity to compare and the
    plain resolved comparison is still right for the ordinary spelling.
    """
    try:
        rt, rf = Path(target).resolve(), Path(folder).resolve()
        if rt == rf or rf in rt.parents:
            return True
    except (OSError, ValueError):
        pass
    wanted = _identity(Path(folder))
    if wanted is None:
        return False
    return any(_identity(p) == wanted for p in _existing_chain(Path(target)))


def contains(folder: Path, target: Path) -> bool:
    """Whether ``folder`` holds ``target`` (``target`` below or equal to it) — :func:`within` reversed."""
    return within(target, folder)


def own_env_file() -> Path:
    """The ``.env`` Chimera's settings are read from: ``Settings``' ``env_file`` in this process's
    working directory, which is where every Settings-screen save writes it."""
    from chimera.config import Settings

    name = Settings.model_config.get("env_file") or ".env"
    return Path.cwd() / str(name)


def is_own_env(target: Path) -> bool:
    """Whether ``target`` is Chimera's own ``.env`` — by identity when it exists, and by the folder
    and the opened name when it does not yet (a write that would create it)."""
    own = own_env_file()
    if _identity(own) is not None and _identity(Path(target)) is not None:
        return same_file(Path(target), own)
    target = Path(target)
    return normal_name(target.name).lower() == own.name.lower() and same_file(
        target.parent, own.parent
    )


def protected_reason(target: Path, home: Path | None) -> str | None:
    """Why no tool or file route may touch ``target``, or None: Chimera's own ``.env`` (its keys and
    its posture) and its data folder (approvals, memory, the ledgers)."""
    if is_own_env(target):
        return "it is Chimera's own .env, which only the Settings screen writes"
    if home is not None and within(target, home):
        return "it is inside Chimera's data folder"
    return None
