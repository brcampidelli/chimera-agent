"""``chimera code list`` / ``chimera code resume <id>`` — a desktop Code conversation, from a terminal.

The app keeps its conversations in ``<home>/code_sessions`` and the terminal surfaces (``chat``,
``tui``) read ``<home>/sessions``: two stores, and the app's home is the platform data directory the
terminal is never told about. So a conversation started on the Code screen could not be continued
anywhere else — except by Claude, through the MCP bridge, which already does exactly this
(``desktop_send`` takes a ``session_id``).

This command is that same door with a person at it. It does not read the app's files and it does not
build an agent: every request goes to the RUNNING app's bridge (``/api/bridge/*``), over the
discovery file the app writes while Settings > "Allow Claude to operate this app" is on, with the
same transport ``chimera mcp desktop`` uses (:func:`chimera.server.desktop_mcp.urllib_call`). The
app's own handler runs the turn — ``POST /api/code/turn``, read as a stream on the app's side — under
the owner's configured posture, writing to the same conversation the screen shows.

**Approvals go through the same gate, or not at all.** A question the turn stops on is printed here
as a card. Answering it is the bridge's ``approve.approval`` route, which is what the screen's card
calls and which the app reserves for "Full control"; without that switch the terminal says so and
points to the app, and keeps watching while the owner answers there. The terminal never decides on
its own: an empty answer leaves the question to the app, and silence there still refuses.

What this deliberately does NOT do: talk to an app on another machine. The bridge's token lives in a
file only this machine can read, and the bridge accepts no other token; a remote client would need
its own streaming client over ``/api/code/turn`` and ``CHIMERA_SERVER_TOKEN`` — the second client the
bridge exists to avoid. ``chimera.server.bind.check_bind`` is untouched: nothing here binds a socket.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from chimera.api.bridge_discovery import read_discovery
from chimera.api.bridge_routes import scrub
from chimera.server.desktop_mcp import NOT_RUNNING, HttpCall, urllib_call

code_app = typer.Typer(
    help="Continue a desktop Code conversation from this terminal, through the running app.",
    no_args_is_help=True,
)
console = Console()

#: How long each poll of a running turn may hold the request. Short, because the bridge returns a
#: turn's text only when a poll returns: this is the granularity the reply streams at.
POLL_SECONDS = 1.0
#: The bridge caps a wait at 300 s; a request is given that plus a margin before it is abandoned.
_HTTP_MARGIN = 30.0

#: Where to send a person who cannot do something from here.
APP_SWITCH = "Settings > \"Allow Claude to operate this app\""
FULL_SWITCH = "Settings > \"Full control\""


class AppUnavailable(RuntimeError):
    """The app cannot be reached, or refused this terminal; the message says which and the fix."""


Ask = Callable[[str], str | None]
"""Read one line from the person; None when there is nobody to ask (end of input)."""


def _ask_stdin(prompt: str) -> str | None:
    try:
        return input(prompt)
    except EOFError:
        return None


@dataclass
class AppCode:
    """The bridge, as the terminal needs it: list, read, send-and-follow, answer.

    The same two seams as :class:`~chimera.server.desktop_mcp.DesktopMCP` — reading the discovery
    file and making one HTTP call — so the whole command is testable with fakes and with a real app
    behind ``TestClient``, and needs nothing the core install does not have.
    """

    discover: Callable[[], dict[str, Any] | None] = read_discovery
    http: HttpCall = urllib_call
    url: str = ""
    token: str = ""
    full: bool = False
    _connected: bool = field(default=False, repr=False)

    def connect(self) -> None:
        found = self.discover()
        if found is None:
            raise AppUnavailable(
                f"{NOT_RUNNING} This terminal reaches the app through that same switch: open the "
                f"app and turn on {APP_SWITCH}."
            )
        self.url = str(found["url"]).rstrip("/")
        self.token = str(found["token"])
        self.full = bool(found.get("full"))
        self._connected = True

    def _clean(self, data: Any) -> Any:
        return scrub(data, [self.token])

    def _check(self, status: int | None, data: Any) -> Any:
        if status is None:
            raise AppUnavailable(
                f"{NOT_RUNNING} (The discovery file is there but nothing answered at {self.url}.)"
            )
        if status == 200:
            return self._clean(data)
        detail = data.get("detail", data) if isinstance(data, dict) else data
        if status == 401:
            raise AppUnavailable(
                "The app rejected this terminal's token — the switch was turned off and on again, "
                "or the app restarted. Run the command again."
            )
        if status == 403:
            raise AppUnavailable(f"Refused by the app: {self._clean(detail)}")
        raise AppUnavailable(f"The app answered {status}: {self._clean(detail)}")

    def call(self, route: str, params: dict[str, Any] | None = None, body: Any = None,
             wait: float = 0.0) -> Any:
        """One bridge call; the route's data, or the job for a streamed route."""
        if not self._connected:
            self.connect()
        payload = {"route": route, "params": params or {}, "body": body, "wait_seconds": wait}
        status, data = self.http(
            "POST", f"{self.url}/api/bridge/call", self.token, payload, wait + _HTTP_MARGIN
        )
        out = self._check(status, data)
        if not isinstance(out, dict):
            raise AppUnavailable(f"The app answered something this terminal cannot read: {out!r}")
        if out.get("job") is not None:
            return out["job"]
        inner = int(out.get("status", 0) or 0)
        if inner >= 400:
            detail = out.get("data")
            detail = detail.get("detail", detail) if isinstance(detail, dict) else detail
            raise AppUnavailable(f"The app answered {inner}: {detail}")
        return out.get("data")

    def poll(self, job_id: str, since: int, wait: float) -> dict[str, Any]:
        query = urlencode({"since": since, "wait_seconds": wait})
        status, data = self.http(
            "GET",
            f"{self.url}/api/bridge/jobs/{quote(job_id, safe='')}?{query}",
            self.token,
            None,
            wait + _HTTP_MARGIN,
        )
        out = self._check(status, data)
        return out if isinstance(out, dict) else {}

    def sessions(self) -> list[dict[str, Any]]:
        rows = self.call("conversations.list")
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def answer(self, request_id: str, approved: bool) -> bool:
        """The screen's own answer route; the app refuses it without Full control."""
        data = self.call(
            "approve.approval", {"request_id": request_id}, {"approved": bool(approved)}
        )
        return bool(isinstance(data, dict) and data.get("ok"))


def _client() -> AppCode:
    """Seam for the tests: they swap the transport, never the logic."""
    return AppCode()


# ---- following one turn ------------------------------------------------------------------------


def new_text(printed: str, text: str) -> str:
    """What of ``text`` has not been printed yet.

    The bridge keeps a turn's text whole up to its last 20,000 characters, then slides. Up to there
    the new part is the suffix; past it the window has moved and the new part starts after the last
    thing printed, found by its tail.
    """
    if text.startswith(printed):
        return text[len(printed):]
    tail = printed[-200:]
    at = text.rfind(tail) if tail else -1
    return text[at + len(tail):] if at >= 0 else text


@dataclass
class TurnOutcome:
    ok: bool
    session_id: str = ""
    error: str = ""


def _render_card(data: dict[str, Any]) -> None:
    console.print()
    console.print("[bold yellow]Approval needed[/bold yellow]")
    console.print(f"  action: {escape(str(data.get('action', '')))}")
    console.print(f"  why:    {escape(str(data.get('reason', '')))}")
    p, band = data.get("p"), data.get("band")
    if isinstance(p, int | float):
        console.print(f"  p:      {float(p):.2f}" + (f" ({escape(str(band))})" if band else ""))
    wait = data.get("wait_seconds")
    if isinstance(wait, int | float) and wait > 0:
        console.print(f"  [dim]unanswered after {wait:.0f} s, it is refused[/dim]")


def _settle(code: AppCode, data: dict[str, Any], ask: Ask) -> None:
    """Show one question and, where this terminal may, take the person's answer to the app."""
    _render_card(data)
    request_id = str(data.get("id") or "")
    if not code.full:
        console.print(
            f"  [dim]Answer it in the Chimera app. Answering from here needs {FULL_SWITCH}, "
            "the same switch the bridge reserves it for. Still watching the turn.[/dim]"
        )
        return
    while True:
        reply = ask("  approve? yes / no (Enter leaves it to the app): ")
        if reply is None or not reply.strip():
            console.print("  [dim]left for the app; still watching the turn[/dim]")
            return
        word = reply.strip().lower()
        if word in {"y", "yes", "n", "no"}:
            approved = word.startswith("y")
            break
        # An answer this important must be typed; a word that is neither is not taken as either.
        console.print("  [yellow]say yes or no[/yellow]")
    if code.answer(request_id, approved):
        console.print(f"  [green]{'approved' if approved else 'refused'}[/green] {escape(request_id)}")
    else:
        console.print(
            "  [yellow]that question had already been answered or had timed out[/yellow]"
        )


def _receipt(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    parts = [str(result.get("model") or "")]
    usd = result.get("usd")
    if isinstance(usd, int | float):
        parts.append(f"${float(usd):.4f}")
    steps = result.get("steps")
    if isinstance(steps, int):
        parts.append(f"{steps} steps")
    return " · ".join(p for p in parts if p)


def follow_turn(code: AppCode, body: dict[str, Any], ask: Ask, *,
                poll_seconds: float = POLL_SECONDS) -> TurnOutcome:
    """Send one message and follow the turn to its end, printing as it goes."""
    job = code.call("conversations.send", {}, body, poll_seconds)
    if not isinstance(job, dict) or not job.get("job_id"):
        raise AppUnavailable(f"The app did not start a turn: {job!r}")
    job_id = str(job["job_id"])
    printed, seen, error = "", 0, ""
    try:
        while True:
            delta = new_text(printed, str(job.get("text") or ""))
            if delta:
                console.print(delta, end="", markup=False, highlight=False)
                printed = (printed + delta)[-20_000:]
            for event in job.get("events") or []:
                n = int(event.get("n", 0) or 0)
                if n <= seen:
                    continue
                seen = n
                kind, data = event.get("event"), event.get("data")
                if kind == "approval" and isinstance(data, dict):
                    _settle(code, data, ask)
                elif kind == "tool" and isinstance(data, dict):
                    mark = "ok" if data.get("ok") else "failed"
                    console.print(f"\n[dim]· {escape(str(data.get('name', '')))} {mark}[/dim]")
                elif kind == "notice" and isinstance(data, dict):
                    console.print(f"\n[yellow]{escape(str(data.get('text', '')))}[/yellow]")
                elif kind == "error":
                    error = str(data.get("message", data) if isinstance(data, dict) else data)
            if job.get("done"):
                break
            job = code.poll(job_id, seen, poll_seconds)
    except KeyboardInterrupt:
        console.print(
            f"\n[yellow]stopped watching — the turn keeps running in the app (job {job_id})[/yellow]"
        )
        raise
    result = job.get("result")
    if not printed and isinstance(result, dict) and result.get("answer"):
        console.print(str(result["answer"]), markup=False, highlight=False)
    error = error or str(job.get("error") or "")
    console.print()
    if error:
        console.print(f"[red]the turn ended with an error:[/red] {escape(error)}")
        return TurnOutcome(ok=False, session_id=str(job.get("session_id") or ""), error=error)
    line = _receipt(result)
    if line:
        console.print(f"[dim]— {escape(line)}[/dim]")
    return TurnOutcome(ok=True, session_id=str(job.get("session_id") or ""))


# ---- the commands ------------------------------------------------------------------------------


def _ago(epoch: Any) -> str:
    try:
        seconds = max(0.0, time.time() - float(epoch))
    except (TypeError, ValueError):
        return ""
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if seconds >= size:
            return f"{seconds / size:.0f} {unit} ago"
    return "just now"


def _fail(exc: AppUnavailable) -> typer.Exit:
    console.print(f"[red]{escape(str(exc))}[/red]")
    return typer.Exit(code=1)


@code_app.command("list")
def code_list(
    limit: int = typer.Option(20, "--limit", "-n", help="How many conversations, newest first."),
) -> None:
    """The desktop app's Code conversations, newest first, with the id `resume` takes."""
    code = _client()
    try:
        rows = code.sessions()
    except AppUnavailable as exc:
        raise _fail(exc) from None
    if not rows:
        console.print("[dim]the app has no Code conversations yet[/dim]")
        return
    table = Table(title="Code conversations")
    for column in ("id", "title", "project", "turns", "updated", ""):
        table.add_column(column)
    for row in rows[: max(1, limit)]:
        workspace = str(row.get("workspace") or "")
        table.add_row(
            str(row.get("id", "")),
            escape(str(row.get("title", ""))[:60]),
            escape(Path(workspace).name or workspace),
            str(row.get("turns", "")),
            _ago(row.get("updated_at")),
            "running" if row.get("running") else "",
        )
    console.print(table)
    console.print("[dim]continue one with: chimera code resume <id>[/dim]")


def _pick(rows: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    """The conversation ``wanted`` names, exactly or by a prefix only one id has."""
    exact = [r for r in rows if str(r.get("id")) == wanted]
    if exact:
        return exact[0]
    matches = [r for r in rows if str(r.get("id", "")).startswith(wanted)] if wanted else []
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AppUnavailable(
            f"No Code conversation {wanted!r} in the app. `chimera code list` shows them."
        )
    ids = ", ".join(str(r.get("id")) for r in matches[:6])
    raise AppUnavailable(f"{wanted!r} could be any of: {ids}. Give more of the id.")


@code_app.command("resume")
def code_resume(
    session_id: str = typer.Argument(..., help="The conversation's id (or a prefix only it has)."),
    message: str = typer.Option(
        None, "--message", "-m", help="Send this one message and exit. Omit to keep talking."
    ),
    model: str = typer.Option(None, "--model", help="Model for these turns; omit for the default."),
) -> None:
    """Continue a desktop Code conversation here; the app runs the turn and shows it too.

    Needs the app open with Settings > "Allow Claude to operate this app" on. Approval questions are
    printed; answering them here also needs "Full control", otherwise answer them in the app.
    """
    code = _client()
    ask: Ask = _ask_stdin
    try:
        row = _pick(code.sessions(), session_id)
    except AppUnavailable as exc:
        raise _fail(exc) from None
    target = str(row.get("id"))
    workspace = str(row.get("workspace") or "")
    console.print(
        f"[bold]{escape(str(row.get('title', '')) or target)}[/bold] "
        f"[dim]{escape(workspace)} · {row.get('turns', 0)} turns · {target}[/dim]"
    )
    if row.get("running"):
        console.print("[yellow]a turn of this conversation is running in the app right now[/yellow]")
    if not code.full:
        console.print(
            f"[dim]approval questions will be shown here and answered in the app ({FULL_SWITCH} "
            "lets this terminal answer them)[/dim]"
        )
    while True:
        text = message if message is not None else ask("you> ")
        if text is None or text.strip() in {"", "/exit", "/quit"}:
            return
        # The conversation's own project, always. Without it the app runs the turn in its default
        # folder and files the conversation under no project — a resume that moves the work.
        body: dict[str, Any] = {"message": text, "session_id": target}
        if workspace:
            body["workspace"] = workspace
        if model:
            body["model"] = model
        try:
            outcome = follow_turn(code, body, ask)
        except AppUnavailable as exc:
            raise _fail(exc) from None
        except KeyboardInterrupt:
            raise typer.Exit(code=130) from None
        if message is not None:
            if not outcome.ok:
                raise typer.Exit(code=1)
            return
