"""Reading through a path of the workspace that, in the end, points somewhere else.

A folder the agent is told to work in can hold a symlink (or, on Windows, a junction) to any place the
process can read: the owner's ``agent.json``, ``.env``, another project. A read that "opens the file
named ``package.json``" then reads that place, and the guards that look at the LAST component of the path
(`is_symlink()`) miss a link in any directory above it, and miss a junction altogether (Python 3.12 does
not report one as a link).

The one rule, kept here so no reader decides it on its own: resolve the whole path to its real location
and require that location to lie inside the resolved workspace.
"""

from __future__ import annotations

import os
from pathlib import Path


def inside_parts(root: tuple[str, ...], real: tuple[str, ...]) -> bool:
    """Whether the path whose components are ``real`` lies at or under the one whose are ``root``.

    EXACT on every component but the first (the drive or the anchor, which Windows spells in either
    case): a volume or a directory can be case-sensitive on Windows (`fsutil file setCaseSensitiveInfo`,
    WSL), where ``repo`` and ``REPO`` are two different folders, and folding the case would call a
    sibling the workspace. Both sides come from ``realpath``, which returns the real case of everything
    that exists, so on a case-insensitive volume a workspace named in another case compares equal.
    """
    if len(real) < len(root) or not root:
        return False
    if root[0].lower() != real[0].lower():
        return False
    return root[1:] == real[1 : len(root)]


def contained_path(workspace: Path | str, path: Path | str) -> Path | None:
    """``path`` resolved to its real location, or None when that is outside ``workspace``.

    ``os.path.realpath`` follows symlinks and junctions in EVERY component and collapses ``..``, so a
    link in a parent directory is judged as the last component is. A path that does not exist resolves
    to where it would be, which is what a caller that is about to create it wants too. Both sides are
    resolved before they are compared (see :func:`inside_parts`).
    """
    try:
        root = Path(os.path.realpath(workspace))
        real = Path(os.path.realpath(path))
    except (OSError, ValueError):  # an embedded NUL, or a path the system cannot resolve
        return None
    return real if inside_parts(root.parts, real.parts) else None


def read_contained_text(
    workspace: Path | str, path: Path | str, *, errors: str = "strict", limit: int | None = None
) -> str | None:
    """The text of ``path`` when it is a file whose real location is inside ``workspace``; else None.

    ``limit`` reads at most that many characters. Unreadable files are None, never an exception: a
    reader of optional project files treats "cannot read" and "not allowed to read" alike.
    """
    real = contained_path(workspace, path)
    if real is None:
        return None
    try:
        if not real.is_file():
            return None
        with real.open(encoding="utf-8", errors=errors) as stream:
            return stream.read() if limit is None else stream.read(limit)
    except (OSError, ValueError):
        return None
