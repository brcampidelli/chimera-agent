"""``chimera code list`` / ``resume <id>`` / ``new "<message>"`` — a Code conversation, from a terminal.

**Two transports since S2.** When the owner's background service runs (``chimera service start``),
every subcommand goes to it through :mod:`chimera.cli.code_service` — the owner's own door, the
same ``/api/code/turn`` stream the Code screen reads — and ``new`` exists only there. Otherwise
``list`` and ``resume`` go through the running app's bridge, as described below; that path is
unchanged, and this module's bridge client (:class:`AppCode`) never reads the service's record or
token. What each door may do is in :mod:`chimera.cli.code_service`.

The app keeps its conversations in ``<home>/code_sessions`` and the terminal surfaces (``chat``,
``tui``) read ``<home>/sessions``: two stores, and the app's home is the platform data directory the
terminal is never told about. So a conversation started on the Code screen could not be continued
anywhere else — except by Claude, through the MCP bridge, which already does exactly this
(``desktop_send`` takes a ``session_id``).

The bridge path is that same door with a person at it. It does not read the app's files and it
does not build an agent: every request goes to the RUNNING app's bridge (``/api/bridge/*``), over the
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
from chimera.cli import code_service
from chimera.cli.code_service import ServiceCode, ServiceUnavailable
from chimera.server.desktop_mcp import NOT_RUNNING, HttpCall, urllib_call

code_app = typer.Typer(
    help="Start or continue a Code conversation from this terminal, through the running service "
    "or app.",
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
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": 50}
            if cursor:
                params["cursor"] = cursor
            page = self.call("conversations.list", params)
            if isinstance(page, list):
                rows.extend(row for row in page if isinstance(row, dict))
                break
            if not isinstance(page, dict):
                break
            items = page.get("items")
            if isinstance(items, list):
                rows.extend(row for row in items if isinstance(row, dict))
            next_cursor = page.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return rows

    def answer(self, request_id: str, approved: bool) -> bool:
        """The screen's own answer route; the app refuses it without Full control."""
        return bool(self.answer_reply(request_id, approved).get("ok"))

    def answer_reply(self, request_id: str, approved: bool) -> dict[str, Any]:
        """The route's whole reply: ``ok``, and why not when the app says (``outcome``/``detail``)."""
        data = self.call(
            "approve.approval", {"request_id": request_id}, {"approved": bool(approved)}
        )
        return data if isinstance(data, dict) else {}


def _client() -> AppCode:
    """Seam for the tests: they swap the transport, never the logic."""
    return AppCode()


def _service() -> code_service.Found:
    """The owner's background service, if one runs and confirms its record. Seam for the tests."""
    return code_service.find()


def _owner_service() -> ServiceCode | None:
    """The service when it is alive; otherwise None, saying why a record on disk was not used."""
    found = _service()
    if found.refused:
        console.print(f"[yellow]{escape(found.refused)}[/yellow]")
    return found.service


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
    answered = code.answer_reply(request_id, approved)
    if answered.get("ok"):
        console.print(f"  [green]{'approved' if approved else 'refused'}[/green] {escape(request_id)}")
    elif answered.get("outcome") == "needs_code":
        # Another process asked it and only that process holds the code an approval must carry
        # (study 30, S30-30). "Already answered or timed out" would be false: it is still waiting.
        console.print(
            "  [yellow]not approved: another Chimera process asked this, and only it holds the "
            f"code. Approve it with {escape(str(answered.get('detail') or ''))} and the code from "
            "the message that asked[/yellow]"
        )
    else:
        console.print(
            "  [yellow]that question had already been answered or had timed out[/yellow]"
        )


def _receipt(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    from chimera.build_info import CHIMERA_VERSION, chimera_git_sha

    parts = [str(result.get("model") or "")]
    usd = result.get("usd")
    if isinstance(usd, int | float):
        parts.append(f"${float(usd):.4f}")
    steps = result.get("steps")
    if isinstance(steps, int):
        parts.append(f"{steps} steps")
    parts.append(f"Chimera {CHIMERA_VERSION}")
    sha = chimera_git_sha()
    if sha:
        parts.append(f"git {sha}")
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
            # `more`: the app pages events (100 at a time); a finished job may still have some to
            # hand over, and leaving at `done` would drop the turn's last tool lines and its error.
            if job.get("done") and not job.get("more"):
                break
            job = code.poll(job_id, seen, 0.0 if job.get("more") else poll_seconds)
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


# ---- the same, through the owner's service -------------------------------------------------------

#: Where the asker says it showed a question's code (`pending.code_shown`) when no code can reach
#: this terminal: only the app's card holds it, or nothing does.
_CODE_NOT_HERE = {
    "screen": "its code is only on the app's card: approve it there",
    "nowhere": "its code was shown nowhere, so it cannot be approved; refuse it or let it time out",
}


def _settle_owner(service: ServiceCode, data: dict[str, Any], ask: Ask) -> None:
    """One question, answered from this terminal with the code it was given — never without.

    The service's token opens the whole API, and in the service's own process the screen's route
    would approve a question its turn asked with no code at all. So a yes from here is only ever
    the answer file carrying the code the owner typed (:meth:`ServiceCode.approve`), checked by the
    asker; a no goes through the route, which needs no code.
    """
    _render_card(data)
    request_id = str(data.get("id") or "")
    not_here = _CODE_NOT_HERE.get(service.code_shown(request_id))
    if not_here:
        console.print(f"  [dim]{not_here}. Here you can refuse it.[/dim]")
    prompt = "  refuse? no (Enter leaves it): " if not_here else (
        "  approve? yes / no (Enter leaves it to the app): "
    )
    while True:
        reply = ask(prompt)
        if reply is None or not reply.strip():
            console.print("  [dim]left unanswered here; still watching the turn[/dim]")
            return
        word = reply.strip().lower()
        if word in {"n", "no"} or (not not_here and word in {"y", "yes"}):
            break
        console.print("  [yellow]say " + ("no" if not_here else "yes or no") + "[/yellow]")
    if word.startswith("n"):
        if service.refuse(request_id):
            console.print(f"  [green]refused[/green] {escape(request_id)}")
        else:
            console.print("  [yellow]that question had already been answered or had timed out[/yellow]")
        return
    code = ask("  code (6 digits, from the message that asked; a wrong one refuses it): ")
    if code is None or not code.strip():
        console.print("  [dim]no code, nothing approved; still watching the turn[/dim]")
        return
    if service.approve(request_id, code):
        console.print(f"  [green]sent with its code[/green] {escape(request_id)} [dim](the asker checks it)[/dim]")
    else:
        console.print(
            "  [yellow]not approved: the code must be the six digits the question was sent with, "
            "and the question must still be waiting[/yellow]"
        )


def _ask_back(service: ServiceCode, data: dict[str, Any], ask: Ask) -> None:
    """The agent's own question: a typed answer is data for the turn, never an approval."""
    console.print()
    console.print(f"[bold cyan]The agent asks:[/bold cyan] {escape(str(data.get('question', '')))}")
    options = [str(o) for o in data.get("options") or []]
    if options:
        console.print("  [dim]options: " + escape(" | ".join(options)) + "[/dim]")
    reply = ask("  answer (Enter leaves it to the app): ")
    if reply is None or not reply.strip():
        console.print("  [dim]left for the app; still watching the turn[/dim]")
        return
    if not service.answer_question(str(data.get("id") or ""), reply.strip()):
        console.print("  [yellow]that answer was not taken (already answered, or not an option)[/yellow]")


def follow_service_turn(service: ServiceCode, body: dict[str, Any], ask: Ask) -> TurnOutcome:
    """Send one message to the service and print the turn as its stream arrives."""
    session_id, turn_id, error, printed = str(body.get("session_id") or ""), "", "", False
    result: Any = None
    try:
        for event, data in service.stream_turn(body):
            frame = data if isinstance(data, dict) else {}
            if event == "session":
                fresh = not session_id
                session_id = str(frame.get("session_id") or session_id)
                turn_id = str(frame.get("turn_id") or "")
                if fresh and session_id:
                    console.print(f"[dim]conversation {escape(session_id)} — the app lists it too[/dim]")
            elif event == "token":
                text = str(frame.get("text") or "")
                printed = printed or bool(text)
                console.print(text, end="", markup=False, highlight=False)
            elif event == "tool":
                mark = "ok" if frame.get("ok") else "failed"
                console.print(f"\n[dim]· {escape(str(frame.get('name', '')))} {mark}[/dim]")
            elif event == "notice":
                console.print(f"\n[yellow]{escape(str(frame.get('text', '')))}[/yellow]")
            elif event == "approval":
                _settle_owner(service, frame, ask)
            elif event == "user_question":
                _ask_back(service, frame, ask)
            elif event == "error":
                error = str(frame.get("message") or data)
            elif event == "done":
                result = frame
    except KeyboardInterrupt:
        console.print(
            "\n[yellow]stopped watching — the turn keeps running in the service"
            + (f" (turn {escape(turn_id)})" if turn_id else "") + "[/yellow]"
        )
        raise
    if not printed and isinstance(result, dict) and result.get("answer"):
        console.print(str(result["answer"]), markup=False, highlight=False)
    console.print()
    if error:
        console.print(f"[red]the turn ended with an error:[/red] {escape(service.clean(error))}")
        return TurnOutcome(ok=False, session_id=session_id, error=error)
    if result is None:
        console.print("[red]the stream ended before the turn did[/red]")
        return TurnOutcome(ok=False, session_id=session_id, error="no done frame")
    line = _receipt(result)
    if line:
        console.print(f"[dim]— {escape(line)}[/dim]")
    return TurnOutcome(ok=True, session_id=session_id)


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


def _fail(exc: AppUnavailable | ServiceUnavailable) -> typer.Exit:
    console.print(f"[red]{escape(str(exc))}[/red]")
    return typer.Exit(code=1)


@code_app.command("list")
def code_list(
    limit: int = typer.Option(20, "--limit", "-n", help="How many conversations, newest first."),
) -> None:
    """The Code conversations, newest first, with the id `resume` takes.

    From the background service when one runs, otherwise from the open app's bridge.
    """
    service = _owner_service()
    try:
        rows = service.sessions() if service is not None else _client().sessions()
    except (AppUnavailable, ServiceUnavailable) as exc:
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


def _talk(
    send: Callable[[dict[str, Any]], TurnOutcome], ask: Ask, message: str | None,
    target: str, workspace: str,
) -> None:
    """Send turns into one conversation until the person stops, or once with ``--message``."""
    while True:
        text = message if message is not None else ask("you> ")
        if text is None or text.strip() in {"", "/exit", "/quit"}:
            return
        # The conversation's own project, always. Without it the app runs the turn in its default
        # folder and files the conversation under no project — a resume that moves the work.
        body: dict[str, Any] = {"message": text, "session_id": target}
        if workspace:
            body["workspace"] = workspace
        # No `model`: since 2026-10-04 which model answers is the owner's decision in the app, and
        # the bridge refuses a turn that names one, at every tier. The option that sent it is gone
        # rather than left to fail on every use.
        try:
            outcome = send(body)
        except (AppUnavailable, ServiceUnavailable) as exc:
            raise _fail(exc) from None
        except KeyboardInterrupt:
            raise typer.Exit(code=130) from None
        if message is not None:
            if not outcome.ok:
                raise typer.Exit(code=1)
            return


@code_app.command("resume")
def code_resume(
    session_id: str = typer.Argument(..., help="The conversation's id (or a prefix only it has)."),
    message: str = typer.Option(
        None, "--message", "-m", help="Send this one message and exit. Omit to keep talking."
    ),
) -> None:
    """Continue a Code conversation here; the app shows the same conversation.

    Through the background service when one runs (`chimera service start`): approval questions
    are answered here with the code the question was sent with, or in the app. Otherwise through
    the open app's bridge, which needs Settings > "Allow Claude to operate this app"; answering
    questions there also needs "Full control". The turns run on the models configured in the app:
    neither door takes a model choice.
    """
    ask: Ask = _ask_stdin
    service = _owner_service()
    code: AppCode | None = None
    try:
        if service is not None:
            rows = service.sessions()
        else:
            code = _client()
            rows = code.sessions()
        row = _pick(rows, session_id)
    except (AppUnavailable, ServiceUnavailable) as exc:
        raise _fail(exc) from None
    target = str(row.get("id"))
    workspace = str(row.get("workspace") or "")
    console.print(
        f"[bold]{escape(str(row.get('title', '')) or target)}[/bold] "
        f"[dim]{escape(workspace)} · {row.get('turns', 0)} turns · {target}[/dim]"
    )
    if row.get("running"):
        console.print("[yellow]a turn of this conversation is running in the app right now[/yellow]")
    if service is not None:
        console.print("[dim]through the background service[/dim]")
        _talk(lambda body: follow_service_turn(service, body, ask), ask, message, target, workspace)
        return
    bridge = code if code is not None else _client()
    if not bridge.full:
        console.print(
            f"[dim]approval questions will be shown here and answered in the app ({FULL_SWITCH} "
            "lets this terminal answer them)[/dim]"
        )
    _talk(lambda body: follow_turn(bridge, body, ask), ask, message, target, workspace)


@code_app.command("new")
def code_new(
    message: str = typer.Argument(..., help="The first message of the new conversation."),
    workspace: str = typer.Option(
        None, "--workspace", "-w",
        help="The project folder. Omitted: the app's default folder, with no project.",
    ),
) -> None:
    """Start a Code conversation from this terminal; the app lists it and can continue it.

    Needs the background service (`chimera service start`), and does not start it. The turn runs
    in the service under the owner's posture; approval questions are answered here with the code
    the question was sent with, or in the app. Continue with `chimera code resume <id>`.
    """
    service = _owner_service()
    if service is None:
        console.print(
            "[red]No background service is running.[/red] Start one with "
            f"[bold]{code_service.START_HINT}[/bold], then run this again. "
            "(This command does not start it, and the app's bridge cannot open conversations.)"
        )
        raise typer.Exit(code=1)
    body: dict[str, Any] = {"message": message}
    if workspace:
        folder = Path(workspace).expanduser().resolve()
        if not folder.is_dir():
            console.print(f"[red]Not a folder: {escape(str(folder))}[/red]")
            raise typer.Exit(code=1)
        body["workspace"] = str(folder)
    try:
        outcome = follow_service_turn(service, body, _ask_stdin)
    except ServiceUnavailable as exc:
        raise _fail(exc) from None
    except KeyboardInterrupt:
        raise typer.Exit(code=130) from None
    if outcome.session_id:
        console.print(f"[dim]continue with: chimera code resume {escape(outcome.session_id)}[/dim]")
    if not outcome.ok:
        raise typer.Exit(code=1)
