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

**The governance gates go the other way, and that is the one place this app blocks itself.** A gate
has to be answered *before* the tool runs, so ``post_message`` — which is fire-and-forget — is the
wrong direction: the worker calls :class:`~chimera.tui.confirm.ModalGate`, which pushes a screen and
waits for it. See that module for why it is safe under ``exclusive=True``, and
`bench/right_hand_governance/RESULTS.md` Part 2 for the 123.8 s hang it replaces.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from rich.markdown import Markdown
from rich.markup import escape
from textual.app import App, ComposeResult, SystemCommand
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import Footer, Header, Input, RichLog, Static

from chimera.core.agent import ToolActivity
from chimera.interface import ChatSession, render
from chimera.interface.render import scrub_provider_ids
from chimera.interface.session import TurnReport
from chimera.tui.activity import ActivityPanel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.api.sessions import SessionManager
    from chimera.cli.right_hand import RightHand
    from chimera.orchestration.budget import SpendBudget
    from chimera.tui.confirm import ModalGate

_SLASH = ["/model ", "/new", "/reset", "/clear", "/stream", "/help", "/exit"]
_HELP = (
    "[b]commands[/b]  /model <slug> · /new (fresh thread) · /reset (same) · "
    "/clear (clear screen) · /stream (toggle live tokens) · /exit\n"
    "[b]keys[/b]  ^R new thread · ^L clear · ^P palette · PgUp/PgDn scroll · ^C quit"
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
        ("ctrl+r", "reset", "New thread"),
        ("ctrl+l", "clear_log", "Clear"),
        ("pageup", "scroll_log('up')", "Scroll"),
        ("pagedown", "scroll_log('down')", ""),
    ]

    def __init__(
        self,
        session: ChatSession,
        *,
        model_label: str = "",
        stream: bool = True,
        fuse: bool = False,
        usage_home: Path | None = None,
        hand: RightHand | None = None,
        gate: ModalGate | None = None,
        budget: SpendBudget | None = None,
        sessions: SessionManager | None = None,
        session_id: str = "",
        resumed: bool = False,
    ) -> None:
        super().__init__()
        self.session = session
        self.model_label = model_label
        self.stream_enabled = stream and not fuse  # never promise a stream fusion can't deliver
        self.fuse = fuse
        self._live = ""
        #: Where to append this run's usage rows, or None for a TUI nobody is billing (the tests).
        #: The panel showed a price per turn and recorded it nowhere, so the Cost screen reported
        #: zero spend for a surface that had been running all day.
        self.usage_home = usage_home
        self.usage_session = uuid4().hex[:12]
        #: The governed stack this conversation runs on, or None for a TUI built without one (the
        #: dispatch tests). Held for `begin_turn` and for the per-turn verdicts, exactly as the
        #: REPL loop holds it.
        self.hand = hand
        #: The thing that makes `hand` answerable here. Bound on mount and released on the way out,
        #: so a question asked while the app is not on screen refuses instead of waiting.
        self.gate = gate
        #: The conversation's dollar ceiling, or None. Shown in the panel, because a ceiling nobody
        #: can see is indistinguishable from a turn that stopped for its own reasons.
        self.budget = budget
        #: The store this thread is saved to, or None for a TUI nobody is persisting (the tests,
        #: and every construction of this class that predates the store arriving here).
        #:
        #: It is the one `chimera chat` writes — ``<home>/sessions`` — and not a second one. A
        #: TUI-only transcript would have been the easier change and would have made a split
        #: permanent between two surfaces that are the same conversation. (The *coding* store,
        #: ``<home>/code_sessions``, stays separate for the reason its own module argues: it keeps
        #: the model's message list and its receipts, which do not survive being flattened into
        #: prose pairs.)
        self.sessions = sessions
        #: Which thread of that store is open. Swapped by :meth:`action_reset`.
        self.session_id = session_id
        #: Whether this run picked up an existing thread. Said out loud on mount, because resuming
        #: in silence is the same surprise as forgetting.
        self.resumed = resumed

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
        self._say_which_thread()
        # The app is on screen: from here a gate's question can be drawn. Before this line it could
        # not, and the gate says so rather than waiting for a timeout to say it for it.
        if self.gate is not None:
            self.gate.bind(self, note=self._append)
        self._show_budget()
        self.query_one("#prompt", Input).focus()

    def on_unmount(self) -> None:
        """Stop claiming a person can be asked, the moment the screen goes away."""
        if self.gate is not None:
            self.gate.release()

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
        elif text in ("/reset", "/new"):
            # Two names for one action, exactly as `chimera chat` spells it. `/reset` kept its name
            # because it is what people type and the surprise would be a command that vanished;
            # what it DOES changed the moment the thread became a file.
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
            if self.budget is not None and self.budget.blocked():
                # Refused here rather than one layer down, for the reason `chat` gives: the loop
                # would refuse at zero cost but would first record a turn whose "answer" is the
                # budget error, and that text is replayed into every later prompt.
                self._append(render.budget_spent_line(str(self.budget.blocked())))
                return
            # Before a single tool runs: the ledger is told whose words this turn is. Without it
            # every fetch reads `unknown` and `CHIMERA_TAINT_AUTHORITY=authority` cannot tell a page
            # the person named from one the model went and found.
            if self.hand is not None:
                self.hand.begin_turn(text)
            self._activity().start_turn(self._busy_label())
            # Disable input for the duration of the turn. A thread worker can't be preempted, so a
            # second Enter would spin up a CONCURRENT send_verbose on the same (non-thread-safe)
            # ChatSession — interleaving the transcript and the live buffer. Re-enabled on finish.
            #
            # It is also what makes the governance modal safe under `exclusive=True`: a second
            # submission would start a second worker in the same group and cancel the first, which
            # is the one worker blocked on the question. It cannot, because there is no way to
            # submit while a turn is running.
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
            self.post_message(TurnFinished(None, note=f"error: {scrub_provider_ids(str(exc))}"))
            return
        self._record_usage(report)
        self.post_message(TurnFinished(report))

    def _record_usage(self, report: TurnReport) -> None:
        """Put this turn in the project's own census. Best-effort, and never fatal to a turn."""
        if self.usage_home is None:
            return
        from chimera.api.usage import record_turn

        record_turn(self.usage_home, self.usage_session, report)

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
        # The reply is on screen first, then what it does not say. `render.refusal_lines` is the
        # REPL's own sentence — `✗ <tool> did not succeed: <reason>` — reused rather than reworded,
        # because a person who has read one surface should not have to learn a second vocabulary to
        # read this one. The activity panel says the same thing in its own column; this is the half
        # that sits under the answer the model gave, which is where the model's paraphrase of a
        # refused command is.
        for line in render.refusal_lines(report):
            log.write(line)
        cut = render.cut_short_line(report)
        if cut:
            log.write(cut)
        if self.hand is not None:
            governance = render.governance_line(
                *self.hand.turn_verdicts(), attended=self.hand.attended
            )
            if governance:
                log.write(governance)
        panel = self._activity()
        panel.set_tokens(report)
        panel.set_memory(report.memory_facts_used, report.memory_layer)
        panel.set_status("done")
        self._show_budget()
        # After the turn and AFTER the reply is on screen. Both halves are `_persist_turn`'s
        # reasons, which this reuses rather than rediscovers: a Ctrl-C and a closed window are how
        # this app usually ends and neither runs a shutdown hook, and a save that raises must not
        # take down a reply that has already been paid for.
        self._persist()

    # -- actions -----------------------------------------------------------
    def action_reset(self) -> None:
        """Start a fresh thread — and, once there is a file, do NOT clear this one in place.

        Clearing in place cost nothing while the transcript lived only in memory. Now that the
        thread is a file, `self.session.reset()` followed by the next `_persist()` would rewrite it
        empty: a command labelled "clear context" would be the one that destroys the conversation.
        `chimera chat` made exactly this move for exactly this reason when it learned to save.
        """
        if self.sessions is None:
            self.session.reset()
            self._append("[dim]context cleared[/dim]")
            self._activity().set_status("idle")
            return
        self.session_id = self.sessions.new()
        self.session = self.sessions.get(self.session_id)
        self._append(f"[dim]new thread {escape(self.session_id)} — the previous one is saved.[/dim]")
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

    def _show_budget(self) -> None:
        """Put what is left of the ceiling in the panel, or leave the row alone when there is none.

        ``--max-usd`` was withheld from this surface when `chat` and `assist` got it, on the stated
        grounds that "the TUI's panel would need to show it". This is that condition met rather than
        waived: a ceiling the person cannot see turns a turn that stopped for money into a turn that
        stopped for no visible reason, which is the same failure as the cross with no explanation.
        """
        if self.budget is None:
            return
        self._activity().set_budget(self.budget.remaining, self.budget.max_usd)

    def _persist(self) -> None:
        """Save this thread, and survive a save that cannot happen.

        Same shape as ``chimera.cli.main._persist_turn``, and for the same reason it was moved
        there: an unwritable home or a full disk must cost the resume, never the reply. A
        conversation you can read but not reopen beats one that was correctly filed and never shown.
        """
        if self.sessions is None:
            return
        try:
            self.sessions.persist(self.session_id)
        except Exception as exc:  # noqa: BLE001 — a thread that cannot be saved is not a dead app
            self._append(f"[yellow]not saved:[/yellow] [dim]{escape(str(exc))}[/dim]")

    def _say_which_thread(self) -> None:
        """Name the thread on the way in, whichever one it is.

        The scrollback starts empty either way — this app redraws nothing it did not render — so
        without this line a resumed conversation and a fresh one are indistinguishable on screen
        while the model can see the difference. Resuming in silence is the same surprise as
        forgetting, one direction over.
        """
        if self.sessions is None:
            return
        if self.resumed:
            turns = len(self.session.turns)
            self._append(
                f"[dim]resuming {escape(self.session_id)} — {turns} turn(s) the model can see "
                "but this screen has not drawn. ^R starts over.[/dim]"
            )
        else:
            self._append(f"[dim]session {escape(self.session_id)} — saved as you go.[/dim]")

    def _activity(self) -> ActivityPanel:
        return self.query_one("#activity", ActivityPanel)

    def _append(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(markup)
