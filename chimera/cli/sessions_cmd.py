"""``chimera sessions`` — the terminal's saved threads, and the app's running coding turns.

Two things live under one word, and they are kept apart on purpose:

- ``chimera sessions`` alone (and ``--delete``) is what it always was: the conversations ``chimera
  chat`` and ``chimera tui`` saved under ``<home>/sessions``.
- ``list`` / ``attach`` / ``logs`` / ``stop`` (S30-66) act on the coding turns running in the
  desktop app — the sessions that keep working in the background after the screen that started
  them went away. No second store: they read the app's own registry of running turns
  (``GET /api/code/turns/running``) and each turn's recorded frames (``GET /api/code/turns/{id}``),
  through the same bridge door ``chimera code`` uses, so the app's guard and the bridge's place
  checks apply exactly as they do to the MCP client.

``attach`` follows a turn as it runs and STEERS it: a line typed while it works is sent as guidance
(``POST /api/code/turns/{id}/guidance``), which the agent reads between two steps — never during a
tool call — as a user message, and which the turn's receipt lists. Governance is unchanged: the turn
keeps the posture it was started under, and an approval it raises is still answered in the app.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from chimera.cli import code_cmd
from chimera.cli.code_cmd import AppCode, AppUnavailable, Ask

sessions_app = typer.Typer(invoke_without_command=True)
console = Console()

#: Between two reads of a followed turn's frames. The route answers at once, so this is the pace.
POLL_SECONDS = 1.0
#: Polls with no new frame before asking the app whether the turn is still running at all: a turn
#: whose process died never writes `done`, and following it forever would be the wrong answer.
_QUIET_POLLS = 5


def _client() -> AppCode:
    """Seam for the tests: they swap the transport, never the logic."""
    return code_cmd._client()


def _fail(exc: AppUnavailable) -> typer.Exit:
    console.print(f"[red]{escape(str(exc))}[/red]")
    return typer.Exit(code=1)


@sessions_app.callback()
def sessions(
    ctx: typer.Context,
    delete: str = typer.Option(None, "--delete", help="Delete a saved terminal thread by id."),
) -> None:
    """List the conversations ``chimera chat`` and ``chimera tui`` have saved, under ``<home>/sessions``.

    Resume one with ``chimera chat -s <id>`` or ``chimera tui -s <id>`` — one store, so a thread
    started on either surface continues on the other. These are the terminal's threads, and the ones
    ``GET /api/sessions`` serves; coding conversations in the desktop app are a different store
    (``<home>/code_sessions``) with a different shape, and are not listed here.

    The subcommands are about the app's coding turns that are running now: ``list`` them,
    ``attach`` to one (follow it, and type to steer it), read its ``logs``, or ``stop`` it.
    """
    if ctx.invoked_subcommand is not None:
        return
    from datetime import datetime

    from chimera.api.sessions import SessionStore
    from chimera.config import get_settings

    store = SessionStore(get_settings().home / "sessions")
    if delete:
        console.print("[dim]deleted[/dim]" if store.delete(delete) else f"[red]no session {delete}[/red]")
        return

    saved = store.list()
    if not saved:
        console.print("[dim]no saved conversations yet — 'chimera chat' starts one.[/dim]")
        return

    table = Table(title=f"{len(saved)} conversation(s)")
    table.add_column("id", style="cyan")
    table.add_column("turns", justify="right")
    table.add_column("last used")
    table.add_column("title")
    for meta in saved:
        when = datetime.fromtimestamp(meta.updated_at).strftime("%Y-%m-%d %H:%M")
        table.add_row(meta.id, str(meta.turns), when, meta.title)
    console.print(table)
    console.print("[dim]resume with: chimera chat -s <id>[/dim]")


# ---- the app's running turns -------------------------------------------------------------------


def _running(code: AppCode) -> list[dict[str, Any]]:
    rows = code.call("conversations.running")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _pick_turn(rows: list[dict[str, Any]], wanted: str) -> str | None:
    """The running turn ``wanted`` names, exactly or by a prefix only one id has; None if none."""
    ids = [str(r.get("turn_id", "")) for r in rows]
    if wanted in ids:
        return wanted
    matches = [i for i in ids if wanted and i.startswith(wanted)]
    if len(matches) > 1:
        raise AppUnavailable(f"{wanted!r} could be any of: {', '.join(matches[:6])}. Give more of the id.")
    return matches[0] if matches else None


def _frames(code: AppCode, turn_id: str, since: int) -> tuple[list[dict[str, Any]], int]:
    data = code.call("conversations.turn", {"turn_id": turn_id, "since": since})
    if not isinstance(data, dict):
        return [], since
    frames = [f for f in data.get("frames") or [] if isinstance(f, dict)]
    return frames, max(since, int(data.get("seq") or since))


def _render(frame: dict[str, Any]) -> bool:
    """Print one recorded frame the way a person reads a turn; True when it is the turn's end."""
    event = frame.get("event")
    if event == "token":
        console.print(str(frame.get("text", "")), end="", markup=False, highlight=False)
    elif event == "tool":
        mark = "ok" if frame.get("ok") else "failed"
        console.print(f"\n[dim]· {escape(str(frame.get('name', '')))} {mark}[/dim]")
    elif event == "edit":
        console.print(f"\n[dim]· edited {escape(str(frame.get('path', '')))}[/dim]")
    elif event == "guidance":
        console.print(f"\n[cyan]» read by the agent:[/cyan] {escape(str(frame.get('text', '')))}")
    elif event == "notice":
        console.print(f"\n[yellow]{escape(str(frame.get('text', '')))}[/yellow]")
    elif event == "error":
        console.print(f"\n[red]the turn ended with an error:[/red] {escape(str(frame.get('message', '')))}")
        return True
    elif event == "done":
        parts = [str(frame.get("stopped_reason") or ""), str(frame.get("model") or "")]
        steps = frame.get("steps")
        if isinstance(steps, int):
            parts.append(f"{steps} steps")
        console.print(f"\n[dim]— {escape(' · '.join(p for p in parts if p))}[/dim]")
        return True
    return False


def _steer(code: AppCode, turn_id: str, text: str) -> bool:
    """Send one line of guidance; say what became of it."""
    try:
        code.call("conversations.guidance", {"turn_id": turn_id}, {"text": text})
    except AppUnavailable as exc:
        console.print(f"[yellow]not sent: {escape(str(exc))}[/yellow]")
        return False
    console.print("[dim]» queued — the agent reads it between two steps[/dim]")
    return True


@sessions_app.command("list")
def sessions_list() -> None:
    """The coding turns running in the app now, oldest first, with the id the others take."""
    code = _client()
    try:
        rows = _running(code)
    except AppUnavailable as exc:
        raise _fail(exc) from None
    if not rows:
        console.print("[dim]nothing is running in the app[/dim]")
        return
    table = Table(title="Running in the app")
    for column in ("turn", "conversation", "project", "started", "asked"):
        table.add_column(column)
    for row in rows:
        table.add_row(
            str(row.get("turn_id", "")),
            str(row.get("session_id", "")),
            escape(str(row.get("workspace", ""))),
            code_cmd._ago(row.get("started_at")),
            escape(str(row.get("message", ""))[:60]),
        )
    console.print(table)
    console.print("[dim]follow and steer one with: chimera sessions attach <turn>[/dim]")


@sessions_app.command("logs")
def sessions_logs(
    turn_id: str = typer.Argument(..., help="The turn's id (a prefix works while it is running)."),
) -> None:
    """What a coding turn has recorded so far — running or finished — as a person reads it."""
    code = _client()
    try:
        target = _pick_turn(_running(code), turn_id) or turn_id
        frames, _seq = _frames(code, target, 0)
    except AppUnavailable as exc:
        raise _fail(exc) from None
    for frame in frames:
        _render(frame)
    console.print()


def follow(
    code: AppCode,
    turn_id: str,
    *,
    ask: Ask | None,
    poll_seconds: float = POLL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Print the turn's frames from the start until it ends; with ``ask``, lines typed steer it."""
    stop_reading = threading.Event()

    def read_lines() -> None:
        assert ask is not None
        while not stop_reading.is_set():
            line = ask("")
            if line is None:
                return
            if line.strip() and not stop_reading.is_set():
                _steer(code, turn_id, line.strip())

    if ask is not None:
        threading.Thread(target=read_lines, daemon=True).start()
    seq, quiet = 0, 0
    try:
        while True:
            frames, seq = _frames(code, turn_id, seq)
            if any(_render(f) for f in frames):
                return
            quiet = 0 if frames else quiet + 1
            if quiet >= _QUIET_POLLS:
                quiet = 0
                if _pick_turn(_running(code), turn_id) is None:
                    # Gone without a last frame: read once more, then say so instead of waiting.
                    frames, seq = _frames(code, turn_id, seq)
                    if not any(_render(f) for f in frames):
                        console.print("\n[yellow]the turn is no longer running[/yellow]")
                    return
            sleep(poll_seconds)
    finally:
        stop_reading.set()


#: A module-level singleton: a list-typed option built in the signature is what B008 flags.
_SAY_OPTION = typer.Option(
    None, "--say", "-m", help="Guidance to send first (repeatable). Then follow the turn."
)


@sessions_app.command("attach")
def sessions_attach(
    turn_id: str = typer.Argument(..., help="The running turn's id, or a prefix only it has."),
    say: list[str] | None = _SAY_OPTION,
    steer: bool = typer.Option(
        None, "--steer/--no-steer",
        help="Send each line typed while following as guidance. Default: on when this is a terminal.",
    ),
) -> None:
    """Follow a running coding turn from its start, and steer it by typing.

    What you type reaches the agent between two steps, never in the middle of a tool call, and is
    listed on the turn's receipt.

    Approvals the turn raises are answered in the app (or with ``chimera code resume`` under Full
    control); attaching changes nothing about what the turn may do.
    """
    import sys

    code = _client()
    try:
        target = _pick_turn(_running(code), turn_id)
    except AppUnavailable as exc:
        raise _fail(exc) from None
    if target is None:
        console.print(
            f"[red]no running turn {escape(turn_id)} — `chimera sessions list` shows them, and "
            "`chimera sessions logs` reads a finished one[/red]"
        )
        raise typer.Exit(code=1)
    for text in say or []:
        _steer(code, target, text)
    interactive = sys.stdin.isatty() if steer is None else steer
    if interactive:
        console.print("[dim]following; type a line and Enter to steer it, Ctrl+C to detach[/dim]")
    try:
        follow(code, target, ask=code_cmd._ask_stdin if interactive else None)
    except AppUnavailable as exc:
        raise _fail(exc) from None
    except KeyboardInterrupt:
        console.print(f"\n[yellow]detached — the turn keeps running in the app ({target})[/yellow]")
        raise typer.Exit(code=130) from None


@sessions_app.command("stop")
def sessions_stop(
    turn_id: str = typer.Argument(..., help="The running turn's id, or a prefix only it has."),
) -> None:
    """Stop a running coding turn. The step in progress finishes first; nothing after it runs."""
    code = _client()
    try:
        target = _pick_turn(_running(code), turn_id)
        if target is None:
            console.print(f"[red]no running turn {escape(turn_id)}[/red]")
            raise typer.Exit(code=1)
        code.call("conversations.stop", {"turn_id": target})
    except AppUnavailable as exc:
        raise _fail(exc) from None
    console.print(f"stopping {target} — the step in progress finishes first")
