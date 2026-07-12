"""Full-screen terminal UI over the conversational :class:`ChatSession`.

A Textual shell with three moving parts:

- a **conversation log** (``#log``) that renders finished replies as Markdown, so fenced code is
  syntax-highlighted;
- a **live buffer** (``#live``) where the model's tokens stream in as they arrive (single-model path);
- an **activity panel** (``#activity``) showing, from real signals, the tools the agent called, the
  token/cost of the turn, and how many memory facts were recalled.

All conversation behaviour lives in ``ChatSession`` (tested separately). The blocking model call runs
in a thread worker; token/tool callbacks marshal to the UI with ``post_message`` (thread-safe and
non-blocking, so a fast stream never stalls the model thread). The pure-dispatch :meth:`reply_to`
seam is kept and unit-tested without an event loop.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rich.markdown import Markdown
from rich.markup import escape
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import Footer, Header, Input, RichLog, Static

from chimera.core.agent import ToolActivity
from chimera.interface import ChatSession
from chimera.interface.session import TurnReport
from chimera.tui.activity import ActivityPanel

_SLASH = ["/model ", "/reset", "/clear", "/stream", "/help", "/exit"]
_HELP = (
    "[b]commands[/b]  /model <slug> · /reset (clear context) · /clear (clear screen) · "
    "/stream (toggle live tokens) · /exit\n"
    "[b]keys[/b]  ^R reset · ^L clear · ^P palette · PgUp/PgDn scroll · ^C quit"
)


class TokenDelta(Message):
    """A streamed text fragment from the model (worker thread → UI)."""

    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class ToolStarted(Message):
    """A tool the agent just ran, with its outcome (worker thread → UI)."""

    def __init__(self, activity: ToolActivity) -> None:
        self.activity = activity
        super().__init__()


class TurnFinished(Message):
    """A turn completed. ``report`` is the activity/answer, or ``note`` for /reset or an error."""

    def __init__(self, report: TurnReport | None, note: str | None = None) -> None:
        self.report = report
        self.note = note
        super().__init__()


class ChimeraTUI(App[None]):
    """Chat with Chimera in a full-screen terminal app."""

    CSS = """
    #body { height: 1fr; }
    #convo { width: 3fr; }
    #log { height: 1fr; border: round $primary; padding: 0 1; }
    #live { height: auto; max-height: 12; color: $text-muted; padding: 0 1; }
    #activity { width: 32; border: round $primary; padding: 0 1; }
    #activity .act-title { text-style: bold; }
    #activity .act-h { color: $accent; text-style: bold; margin-top: 1; }
    #prompt { dock: bottom; }
    """
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+r", "reset", "Reset"),
        ("ctrl+l", "clear_log", "Clear"),
        ("pageup", "scroll_log('up')", "Scroll"),
        ("pagedown", "scroll_log('down')", ""),
    ]

    def __init__(
        self, session: ChatSession, *, model_label: str = "", stream: bool = True, fuse: bool = False
    ) -> None:
        super().__init__()
        self.session = session
        self.model_label = model_label
        self.stream_enabled = stream and not fuse  # never promise a stream fusion can't deliver
        self.fuse = fuse
        self._live = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Horizontal(id="body"):
            with Vertical(id="convo"):
                yield RichLog(id="log", wrap=True, markup=True, highlight=False)
                yield Static("", id="live", markup=True)
            yield ActivityPanel(id="activity")
        yield Input(
            id="prompt",
            suggester=SuggestFromList(_SLASH, case_sensitive=False),
            placeholder="Message Chimera…  ( /help for commands )",
        )
        yield Footer()

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("Reset context", "Forget the conversation", self.action_reset)
        yield SystemCommand("Clear transcript", "Clear the on-screen log", self.action_clear_log)
        yield SystemCommand("Toggle streaming", "Live token streaming on/off", self._toggle_stream)

    def on_mount(self) -> None:
        self.title = "Chimera"
        self.sub_title = self.model_label or "your right-hand"
        self._append("[bold]Chimera[/bold] — type a message. /help for commands, /exit quits.")
        self.query_one("#prompt", Input).focus()

    # -- input + commands --------------------------------------------------
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not text:
            return
        if text in ("/exit", "/quit", "/q"):
            self.exit()
        elif text == "/help":
            self._append(_HELP)
        elif text == "/clear":
            self.action_clear_log()
        elif text == "/reset":
            self.action_reset()
        elif text == "/stream":
            self._toggle_stream()
        elif text.startswith("/model"):
            slug = text[len("/model") :].strip() or None
            self.session.set_model(slug)
            self.sub_title = slug or self.model_label or "your right-hand"
            self._append(f"[dim]model → {escape(slug or 'default')}[/dim]")
        else:
            # escape() the untrusted text so brackets can't crash Rich's markup parser (e.g. "[/]").
            self._append(f"[bold green]you ›[/bold green] {escape(text)}")
            self._activity().start_turn(self._busy_label())
            # Disable input for the duration of the turn. A thread worker can't be preempted, so a
            # second Enter would spin up a CONCURRENT send_verbose on the same (non-thread-safe)
            # ChatSession — interleaving the transcript and the live buffer. Re-enabled on finish.
            self.query_one("#prompt", Input).disabled = True
            self.run_worker(lambda: self._respond(text), thread=True, exclusive=True)

    # -- testable dispatch (no event loop) ---------------------------------
    def reply_to(self, text: str) -> str | None:
        """Produce a reply for one message, or ``None`` for the /reset command."""
        if text == "/reset":
            self.session.reset()
            return None
        return self.session.send(text)

    # -- worker (thread) ---------------------------------------------------
    def _respond(self, text: str) -> None:
        try:
            report = self.session.send_verbose(
                text,
                on_token=self._emit_token if self.stream_enabled else None,
                on_tool=self._emit_tool,
            )
        except Exception as exc:  # noqa: BLE001 — keep the TUI alive on transient errors
            self.post_message(TurnFinished(None, note=f"error: {exc}"))
            return
        self.post_message(TurnFinished(report))

    def _emit_token(self, delta: str) -> None:
        self.post_message(TokenDelta(delta))

    def _emit_tool(self, activity: ToolActivity) -> None:
        self.post_message(ToolStarted(activity))

    # -- UI-thread handlers ------------------------------------------------
    def on_token_delta(self, message: TokenDelta) -> None:
        self._live += message.text
        self.query_one("#live", Static).update(f"[magenta]chimera ›[/magenta] {escape(self._live)}▌")

    def on_tool_started(self, message: ToolStarted) -> None:
        self._activity().add_tool(message.activity)

    def on_turn_finished(self, message: TurnFinished) -> None:
        self._live = ""
        self.query_one("#live", Static).update("")
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False  # re-open input for the next turn (also on the error path below)
        prompt.focus()
        if message.report is None:
            self._append(f"[dim]{escape(message.note or 'done')}[/dim]")
            self._activity().set_status("idle")
            return
        report = message.report
        log = self.query_one("#log", RichLog)
        log.write("[bold magenta]chimera ›[/bold magenta]")
        log.write(Markdown(report.answer))  # renders fenced code with syntax highlighting
        panel = self._activity()
        panel.set_tokens(report)
        panel.set_memory(report.memory_facts_used, report.memory_layer)
        panel.set_status("done")

    # -- actions -----------------------------------------------------------
    def action_reset(self) -> None:
        self.session.reset()
        self._append("[dim]context cleared[/dim]")
        self._activity().set_status("idle")

    def action_clear_log(self) -> None:
        self.query_one("#log", RichLog).clear()

    def action_scroll_log(self, direction: str) -> None:
        log = self.query_one("#log", RichLog)
        log.scroll_up() if direction == "up" else log.scroll_down()

    def _toggle_stream(self) -> None:
        if self.fuse:
            self._append("[dim]streaming stays off under --fuse (fusion has no token stream)[/dim]")
            return
        self.stream_enabled = not self.stream_enabled
        self._append(f"[dim]streaming {'on' if self.stream_enabled else 'off'}[/dim]")

    # -- helpers -----------------------------------------------------------
    def _busy_label(self) -> str:
        if self.fuse:
            return "fusion — synthesizing (no token stream)"
        return "streaming…" if self.stream_enabled else "thinking…"

    def _activity(self) -> ActivityPanel:
        return self.query_one("#activity", ActivityPanel)

    def _append(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(markup)
