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


def read_text_for_edit(path: Path) -> tuple[str, str]:
    """Read a UTF-8 file for editing: content normalized to ``\\n`` + the file's original newline.

    Reads bytes (not ``read_text``, whose universal-newline translation would silently rewrite every
    line ending on write-back). The content is normalized to ``\\n`` so a model's ``\\n``-based match
    string anchors on a CRLF file too; the original newline (``\\r\\n`` or ``\\n``) is returned so the
    write step can restore the file's own convention exactly. Raises ``UnicodeDecodeError`` on a
    non-UTF-8 file so the caller can report it cleanly instead of corrupting binary content.
    """
    raw = Path(path).read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace("\r\n", "\n"), newline


def atomic_write_text(path: Path, text: str, *, newline: str = "\n") -> None:
    """Write ``text`` as UTF-8 atomically (temp + replace), restoring ``newline`` — no OS translation.

    Byte-level write (not ``write_text``) so a "surgical" edit can't flip untouched lines to the
    platform's line ending. When ``newline == "\\r\\n"`` the normalized ``\\n`` content is converted
    back to CRLF (``text`` is ``\\n``-only after :func:`read_text_for_edit`, so there is no doubling).
    Temp+replace means a crash/error mid-write can't truncate the user's existing file.
    """
    body = text.replace("\n", "\r\n") if newline == "\r\n" else text
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".chimera-tmp")
    tmp.write_bytes(body.encode("utf-8"))
    tmp.replace(p)
