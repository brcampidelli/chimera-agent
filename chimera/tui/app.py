"""Full-screen terminal UI over the conversational :class:`ChatSession`.

A thin Textual shell: a scrolling conversation log, an input box, and a status
bar. All conversation behaviour lives in ``ChatSession`` (tested separately); the
only logic here is dispatch (:meth:`ChimeraTUI.reply_to`), which is unit-tested
without an event loop. Blocking model calls run in a thread worker so the UI
stays responsive.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Input, RichLog

from chimera.interface import ChatSession


class ChimeraTUI(App[None]):
    """Chat with Chimera in a full-screen terminal app."""

    CSS = """
    #log { height: 1fr; border: round $primary; padding: 0 1; }
    #prompt { dock: bottom; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(self, session: ChatSession, *, model_label: str = "") -> None:
        super().__init__()
        self.session = session
        self.model_label = model_label

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield RichLog(id="log", wrap=True, markup=True, highlight=False)
        yield Input(id="prompt", placeholder="Message Chimera…  ( /model <slug> · /reset · /exit )")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Chimera"
        self.sub_title = self.model_label or "your right-hand"
        self._append("[bold]Chimera[/bold] — type a message. /reset clears context, /exit quits.")
        self.query_one("#prompt", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#prompt", Input).value = ""
        if not text:
            return
        if text in ("/exit", "/quit", "/q"):
            self.exit()
            return
        if text.startswith("/model"):
            slug = text[len("/model") :].strip() or None
            self.session.set_model(slug)
            self._append(f"[dim]model → {escape(slug or 'default')}[/dim]")
            return
        # escape() the untrusted text so brackets can't crash Rich's markup parser (e.g. "[/]") or
        # forge styling/links; our own tags stay literal.
        self._append(f"[bold green]you ›[/bold green] {escape(text)}")
        self.run_worker(lambda: self._respond(text), thread=True, exclusive=True)

    # -- testable dispatch (no event loop) ---------------------------------
    def reply_to(self, text: str) -> str | None:
        """Produce a reply for one message, or ``None`` for the /reset command."""
        if text == "/reset":
            self.session.reset()
            return None
        return self.session.send(text)

    # -- worker + rendering ------------------------------------------------
    def _respond(self, text: str) -> None:
        try:
            reply = self.reply_to(text)
        except Exception as exc:  # noqa: BLE001 — keep the TUI alive on transient errors
            reply = f"error: {exc}"
        rendered = (
            "[dim]context cleared[/dim]"
            if reply is None
            else f"[bold magenta]chimera ›[/bold magenta] {escape(reply)}"
        )
        self.call_from_thread(self._append, rendered)

    def _append(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(markup)
