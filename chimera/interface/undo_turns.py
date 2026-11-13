"""``/undo N`` without a console: the preview and the restoration, as lines, for any surface.

``chat`` and ``assist`` print them with Rich and ask with ``typer.confirm``; the full-screen app
prints them into its log and asks with a modal. The two halves are separate on purpose: the
question sits BETWEEN them, and each surface asks in the only way it can. Nothing here asks, and
nothing here restores until :func:`apply` is called, so a surface that cannot ask has no way to
apply by accident.

Goes through :class:`~chimera.api.durable_undo.DurableUndo` — the store the desktop's Code screen
uses — so a file edited again after the turn is kept, not overwritten, and the answer survives a
restart. The oldest of the last ``count`` turns is undone as well (``before=True``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.markup import escape

UNDO_USAGE = "[dim]usage: /undo [N] — N is a whole number, 1 or more[/dim]"
UNDO_QUESTION = "Undo these file changes?"
UNDO_CANCELLED = "[dim]undo cancelled[/dim]"


def bind_thread(session: Any, thread_id: str) -> None:
    """Name the thread a session's retained undo records under, if it keeps any.

    Without a name the session measures a turn and records nothing, so ``/undo N`` would find no
    retained change on a surface that never bound one. A session with no durable store (a fake, the
    messaging gateway) is left as it is.
    """
    if getattr(session, "durable_undo", None) is None:
        return
    session.durable_session_id = thread_id
    session._durable_turn_number = len(getattr(session, "turns", ()))


def parse_count(raw: str) -> int | None:
    """``"2"`` -> 2; anything that is not a whole number of 1 or more -> None."""
    raw = raw.strip()
    if not raw.isdigit() or int(raw) < 1:
        return None
    return int(raw)


@dataclass
class UndoPreview:
    """What ``/undo N`` would touch. ``targets`` empty means there is nothing to ask about."""

    lines: list[str] = field(default_factory=list)
    targets: list[dict[str, Any]] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)

    @property
    def askable(self) -> bool:
        return bool(self.targets)

    @property
    def header(self) -> str:
        return f"will undo the last {len(self.targets)} turn(s) with file changes:"

    def plain_body(self) -> str:
        """The same preview as plain text, for a dialog that does not parse markup."""
        return "\n".join([self.header, *("  " + path for path in self.paths)])


def preview(session: Any, count: int, *, home: Path, usage_id: str) -> UndoPreview:
    """The files of the last ``count`` retained turns. Restores nothing."""
    from chimera.api.durable_undo import DurableUndo

    workspace = getattr(session, "workspace", None)
    if workspace is None:
        return UndoPreview(lines=["[dim]undo is unavailable without a workspace[/dim]"])
    store = getattr(session, "durable_undo", None) or DurableUndo(home)
    turns = store.list_turns(usage_id, Path(workspace))
    targets = turns[-count:]
    if not targets:
        return UndoPreview(lines=["[dim]no retained file changes to undo[/dim]"])
    paths = sorted({path for turn in targets for path in turn["files"]})
    shown = UndoPreview(targets=targets, paths=paths)
    shown.lines = [f"[yellow]{shown.header}[/yellow]", *("  " + escape(path) for path in paths)]
    return shown


def apply(session: Any, shown: UndoPreview, *, home: Path, usage_id: str) -> list[str]:
    """Put the previewed turns back, after the person said yes. Returns the lines to print."""
    from chimera.api.durable_undo import DurableUndo

    workspace = getattr(session, "workspace", None)
    if workspace is None or not shown.askable:
        return ["[dim]undo is unavailable without a workspace[/dim]"]
    store = getattr(session, "durable_undo", None) or DurableUndo(home)
    targets = shown.targets
    result = store.revert_to(usage_id, targets[0]["turn_id"], Path(workspace), before=True)
    if not result.get("ok"):
        return [
            "[yellow]undo not applied:[/yellow] " + escape(str(result.get("reason", "unknown error")))
        ]
    lines = [f"[green]restored {result.get('restored', 0)} file(s)[/green]"]
    for label, key in (
        ("kept, changed again after the turn", "kept"),
        ("kept, created by the turn", "left_new_files"),
    ):
        if result.get(key):
            lines.append(f"[yellow]{label}:[/yellow] " + escape(", ".join(result[key])))
    done = [
        p
        for p in shown.paths
        if p not in result.get("kept", []) and p not in result.get("left_new_files", [])
    ]
    if done and hasattr(session, "_pending_note"):
        session._last_change = None  # a plain /undo must not take the same turn back twice
        session._pending_note = (
            "The person undid the file changes of the last "
            f"{len(targets)} turn(s); these files are back to how they were before: "
            + ", ".join(done)
            + ". Re-read them before relying on what those turns wrote."
        )
    return lines
