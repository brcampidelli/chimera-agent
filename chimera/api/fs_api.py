"""Read-only filesystem helpers for the Code screen: a lazy one-level tree + a single file read.

Both are path-scoped by :func:`~chimera.tools.workspace.resolve_in_workspace` — the exact guard the
file tools use — so a ``..`` or absolute escape raises ``PathEscapesWorkspaceError`` (the endpoint
maps it to HTTP 400). Neither ever raises on a binary/dir/missing file: they degrade to an honest
note. The tree is lazy (immediate children only) so a huge repo doesn't serialize at once, and prunes
the same build/VCS dirs the checkpoint guard skips.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.core.checkpoint import _IGNORE_DIRS
from chimera.tools.workspace import resolve_in_workspace

_MAX_READ_CHARS = 20_000  # mirrors ReadFileTool's cap


def list_tree(workspace: Path, rel: str, *, max_entries: int = 500) -> dict[str, Any]:
    """List the IMMEDIATE children of ``rel`` inside ``workspace`` (dirs first, then files, A→Z).

    Prunes ``_IGNORE_DIRS`` (``.git``, ``node_modules``, ``.chimera``, …), caps at ``max_entries``
    (flagging ``capped``), and returns each child's path relative to the workspace so the UI can
    expand/open it. A ``rel`` that is a file (not a dir) yields an empty list — never an error here.
    """
    root = Path(workspace).resolve()
    target = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    entries: list[dict[str, Any]] = []
    capped = False
    if target.is_dir():
        children = sorted(
            (p for p in target.iterdir() if p.name not in _IGNORE_DIRS),
            key=lambda p: (not p.is_dir(), p.name.lower()),
        )
        for child in children:
            if len(entries) >= max_entries:
                capped = True
                break
            entries.append(
                {
                    "name": child.name,
                    "path": child.relative_to(root).as_posix(),
                    "is_dir": child.is_dir(),
                }
            )
    return {"workspace": str(root), "path": rel, "entries": entries, "capped": capped}


def read_file(workspace: Path, rel: str) -> dict[str, Any]:
    """Read ``rel`` as UTF-8 text (capped at ``_MAX_READ_CHARS``), mirroring ``ReadFileTool``.

    A directory, a binary/undecodable file, or a missing path returns an empty ``content`` + a short
    ``note`` — never a raise (except a path escape, which the caller turns into a 400).
    """
    root = Path(workspace).resolve()
    path = resolve_in_workspace(root, rel)  # raises PathEscapesWorkspaceError on escape
    if path.is_dir():
        return {"path": rel, "content": "", "truncated": False, "note": "binary or non-text"}
    if not path.is_file():
        return {"path": rel, "content": "", "truncated": False, "note": "not found"}
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return {"path": rel, "content": "", "truncated": False, "note": "binary or non-text"}
    truncated = len(text) > _MAX_READ_CHARS
    if truncated:
        text = text[:_MAX_READ_CHARS]
    return {"path": rel, "content": text, "truncated": truncated, "note": ""}
