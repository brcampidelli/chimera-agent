"""Workspace rooting and path-safety for tools that touch the filesystem.

File and shell tools operate relative to a *workspace root* and must not escape it.
This is the first, cheap line of defense; the governance kernel (M5) adds the
policy layer (allow/warn/block/review) on top.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class BoundaryQuestion:
    """What a tool asks when a path leaves the project folder — the shape the approvers read.

    ``reason`` is the sentence the card shows verbatim; ``action`` names the tool and the absolute
    path; ``decision`` is the level (``review``: a question, never a block). Same three fields the
    taint ledger's assessment carries, so the same approver — and the same card — serves both.
    """

    reason: str
    action: str
    decision: str = "review"
    span: str = ""


#: The approver a tool asks before touching a path outside its workspace: ``None`` refuses, as the
#: jail always has. Set by the assembly that has a person to ask (`assemble_registry`, when a
#: screen is bound), never by a tool itself.
AskOutside = Callable[..., bool]


def resolve_for(tool: Any, path: str, *, verb: str) -> Path:
    """The path ``tool`` may touch for ``path``: inside the workspace as always — or outside it,
    if somebody at a screen said so.

    Until 0.59.0 a path outside the project folder was a refusal on every surface, and a refusal
    was the right answer where nobody could be asked. On the desktop somebody can: the taint ledger
    and the policy kernel already put a card on the screen and wait for it (`code_api._owner_allows`),
    and the workspace jail was the one boundary that still answered *no* to a person who would have
    said *yes* — "save the report in Documents" was an error, not a question. ``ask_outside`` on the
    tool is that approver; a tool without one keeps the old answer exactly.

    The declared write region is not consulted here and is not softened by a yes: it is the
    fail-closed boundary the injection defence stands on, and the write tools check it after this.
    A path a person approved and the region refuses is refused.
    """
    workspace: Path = tool.workspace
    try:
        return resolve_in_workspace(workspace, path)
    except PathEscapesWorkspaceError:
        ask: AskOutside | None = getattr(tool, "ask_outside", None)
        if ask is None:
            raise
        root = workspace.resolve()
        candidate = (root / path).resolve()
        name = str(getattr(tool, "name", "") or "tool")
        question = BoundaryQuestion(
            reason=f"{verb} outside the project folder: {candidate} — the project is {root}",
            action=f"{name}: {candidate}",
        )
        if ask(question):
            return candidate
        raise PathEscapesWorkspaceError(
            f"path {path!r} escapes workspace {root} — a person was asked and refused. Do not retry."
        ) from None


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


def shown_path(workspace: Path, path: Path) -> str:
    """How a tool names ``path`` back to the model: relative to the workspace when inside it, the
    absolute path when a person let it outside. ``relative_to`` alone raised on the second case —
    after the write had already landed."""
    root = workspace.resolve()
    candidate = Path(path).resolve()
    if candidate == root or root in candidate.parents:
        return candidate.relative_to(root).as_posix()
    return str(candidate)
