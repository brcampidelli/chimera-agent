"""Workspace rooting and path-safety for tools that touch the filesystem.

File and shell tools operate relative to a *workspace root* and must not escape it.
This is the first, cheap line of defense; the governance kernel (M5) adds the
policy layer (allow/warn/block/review) on top.
"""

from __future__ import annotations

from pathlib import Path


class PathEscapesWorkspaceError(ValueError):
    """Raised when a requested path resolves outside the workspace root."""


def resolve_in_workspace(workspace: Path, path: str) -> Path:
    """Resolve ``path`` against ``workspace`` and ensure it stays inside it.

    Absolute paths and ``..`` traversal that escape the root are rejected.
    """
    root = workspace.resolve()
    candidate = (root / path).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathEscapesWorkspaceError(f"path {path!r} escapes workspace {root}")
    return candidate
