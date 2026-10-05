"""Workspace rooting and path-safety for tools that touch the filesystem.

File and shell tools operate relative to a *workspace root* and must not escape it.
This is the first, cheap line of defense; the governance kernel (M5) adds the
policy layer (allow/warn/block/review) on top.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class PathEscapesWorkspaceError(ValueError):
    """Raised when a requested path resolves outside the workspace root."""


class ProtectedPathError(PathEscapesWorkspaceError):
    """The path is Chimera's own ``.env`` or inside its data folder (`chimera/core/own_files.py`).

    A subclass of the escape error so every caller that already refuses an escape refuses this too,
    with no new branch to forget."""


def chimera_home() -> Path | None:
    """The data folder the running process uses, or None when settings cannot be read."""
    try:
        from chimera.config import get_settings

        return Path(get_settings().home).expanduser()
    except Exception:  # noqa: BLE001 — a guard must not crash a tool; None protects the .env only
        return None


def own_env_readable() -> bool:
    """Whether the owner lets the agent's read tools read Chimera's own ``.env``.

    ``CHIMERA_AGENT_READS_OWN_ENV``, on by default (the owner's decision of 2026-10-04). Read at
    every call, so a change applies from the next tool call. Settings that cannot be read leave the
    default.
    """
    try:
        from chimera.config import get_settings

        return bool(get_settings().agent_reads_own_env)
    except Exception:  # noqa: BLE001 — a guard must not crash a tool; the default is the answer
        return True


def hides_own_env(path: Path, *, forced: bool = False) -> bool:
    """Whether a read tool must leave ``path`` out: it is Chimera's own ``.env`` and the owner has
    switched the agent's reading of it off. Recognised by identity (`chimera/core/own_files.py`), so
    ``ENV~1``, ``.env `` and ``.env::$DATA`` are the same file here."""
    if not forced and own_env_readable():
        return False
    from chimera.core.own_files import is_own_env

    return is_own_env(path)


def refuse_own_env_read(candidate: Path, *, forced: bool = False) -> None:
    """Refuse a read of Chimera's own ``.env`` when the owner has switched that off."""
    if hides_own_env(candidate, forced=forced):
        raise ProtectedPathError(
            f"{candidate} is Chimera's own .env, and the owner keeps it from the agent's read "
            "tools (Settings › Security › Privacy). Do not retry."
        )


def refuse_own_files(candidate: Path, verb: str) -> None:
    """Refuse ``candidate`` when it is Chimera's own ``.env`` or inside its data folder.

    For the agent's write tools (every turn, every posture) and the app's file routes (every
    caller). A workspace that happens to contain the install folder — an app started from the home
    directory — put both within reach of `write_file`: the posture and the keys in ``.env``, and the
    approval queue in ``<home>/approvals``, where a written answer file answers a question. Another
    project's ``.env`` is ordinary work and is not touched by this: only Chimera's own, by identity.
    """
    from chimera.core.own_files import protected_reason

    reason = protected_reason(candidate, chimera_home())
    if reason is not None:
        raise ProtectedPathError(f"no tool may {verb} {candidate}: {reason}. Do not retry.")


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
        inside = resolve_in_workspace(workspace, path)
    except PathEscapesWorkspaceError:
        ask: AskOutside | None = getattr(tool, "ask_outside", None)
        if ask is None:
            raise
        root = workspace.resolve()
        candidate = (root / path).resolve()
        # Before the question, not after it: a yes cannot grant this one (see SHELL_OWNED_ENV).
        _refuse_shell_owned(candidate, verb, hidden=bool(getattr(tool, "hide_own_env", False)))
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
    _refuse_shell_owned(inside, verb, hidden=bool(getattr(tool, "hide_own_env", False)))
    return inside


#: The desktop shell's own files, by the variables the shell names them in when it starts the
#: backend (`start_sidecar` in main.rs; read by `chimera/api/shell_prefs.py`). No tool writes or edits
#: them — inside a workspace that happens to contain the app's folder, or outside one with a
#: person's yes: one key there asks the operating system to start the app at sign-in, and the door
#: to it is the Settings screen, behind the server's token. An approval card that says "write:
#: …/shell-prefs.json" is not a question a person can be expected to read as "start this program
#: every time I sign in". Reading them stays allowed; there is nothing secret in either.
SHELL_OWNED_ENV = ("CHIMERA_SHELL_PREFS", "CHIMERA_SHELL_STATE")
_READ_VERBS = frozenset({"read", "list", "search"})


def _refuse_shell_owned(candidate: Path, verb: str, *, hidden: bool = False) -> None:
    if verb in _READ_VERBS:
        # Reading: only Chimera's own `.env`, and only when the owner switched that off. A listing
        # or a search ROOTED at a folder is not refused for holding it; the tools leave the file
        # out of what they return (`hides_own_env`).
        if verb == "read":
            refuse_own_env_read(candidate, forced=hidden)
        return
    # Chimera's own `.env` and data folder, for every write a tool makes — inside the workspace or
    # outside it with a person's yes, which cannot grant this one either.
    refuse_own_files(candidate, verb)
    for env in SHELL_OWNED_ENV:
        owned = os.environ.get(env, "").strip()
        if owned and Path(owned).resolve() == candidate:
            raise PathEscapesWorkspaceError(
                f"{candidate} belongs to the desktop app and no tool may {verb} it; its switches are "
                "changed in Settings › General › Window and tray. Do not retry."
            )


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
