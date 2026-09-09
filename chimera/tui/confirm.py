"""The question ``chimera tui`` can actually draw — one modal, both gates.

Every other surface asks by writing to a file descriptor. The TUI cannot: Textual's driver owns the
terminal and holds it in raw mode, so a ``typer.confirm`` on stdin waits for bytes that will never
arrive. That is not a theory. Measured in a pty on 2026-09-08
(`bench/right_hand_governance/RESULTS.md` Part 2): the prompt is **never drawn**, the turn blocks
**123.8 s** against ``PROMPT_TIMEOUT_SECONDS = 120``, and comes back ``✗ run_shell`` with no reason
at all — so the only account of what happened is the model's paraphrase of it. `chimera chat`, same
prompt and same model, drew the question at t+12.9 s and honoured the answer.

That measurement is why `#400` governed `chat` and `assist` and deliberately left this surface
alone: adding the taint approver on top of a stdin prompt would have traded *runs without asking*
for *hangs two minutes, once per narrowed call*. This module is the thing that had to exist first.

**Four decisions, each with a plausible-looking alternative.**

**It does not read stdin, at all.** :class:`ConfirmScreen` is a ``ModalScreen`` posted to the app's
message pump and drawn by the UI thread, which is the only thread Textual permits to touch a widget.
Nothing here calls ``input()``, ``typer.confirm`` or ``sys.stdin``.

**The worker thread blocks on the UI thread's answer, and that is safe under ``exclusive=True``.**
The turn runs in ``run_worker(..., thread=True, exclusive=True)`` (`chimera/tui/app.py`), and
:meth:`ModalGate._ask` calls ``app.call_from_thread(app.push_screen_wait, screen)`` from inside it.
Two things make that work rather than deadlock:

* ``push_screen(wait_for_dismiss=True)`` refuses unless ``get_current_worker()`` succeeds. It does:
  ``call_from_thread`` reaches the loop through ``call_soon_threadsafe``, whose ``Handle`` copies
  the **calling thread's** context — and Textual's ``Worker._run_threaded`` sets ``active_worker``
  inside that thread. The ContextVar therefore travels with the callback. This is asserted rather
  than assumed: ``test_the_modal_is_drawn_and_answered_without_touching_stdin``.
* ``exclusive=True`` cancels the *previous* worker in the group when a new one starts, and no new
  one can start while a question is up: the prompt ``Input`` is disabled for the whole turn, so the
  person cannot submit a second message. The modal is a screen, not a worker, so pushing it starts
  nothing that could cancel the turn waiting on it. And one worker thread means one question at a
  time by construction — there is no queue to manage because there cannot be a second asker.

**Silence still refuses, and now it says so.** The screen carries its own countdown and dismisses
itself with ``TIMED_OUT`` when it runs out, so the refusal happens for the same reason it always
did — and, unlike the 123.8 s block, it is on screen while it is being decided, and a line is
written under the reply after it fires. A modal that adds refusals without adding reasons would
make this surface harder to read, not easier.

**When the app cannot draw, it says so instead of pretending.** An unbound gate — the app is
starting, has fallen back, or has shut down — refuses and records that nobody could be asked. That
is the same posture ``chimera/api/app.py`` takes with :func:`declare_no_human_here`, and it is the
half the old code got wrong: ``_human_can_answer()`` returned True inside the TUI because stdin
genuinely is a tty, which is the lie that produced the hang.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from textual.app import App

_log = get_logger("tui.confirm")

#: The three answers a question can come back with. ``TIMED_OUT`` is not folded into ``NO`` because
#: they call for different reactions: one is a decision, the other is the absence of one, and the
#: line printed under the reply has to be able to tell the person which they got.
YES = "yes"
NO = "no"
TIMED_OUT = "timeout"

#: What a refusal says when there was no app to draw on. Distinct from the person saying no, for the
#: same reason ``render.governance_line`` distinguishes them: "refused" and "refused because nobody
#: could be asked" are different sentences to the person reading them.
NO_SCREEN = "the TUI had no screen to ask on, so the answer is no"


class ConfirmScreen(ModalScreen[str]):
    """One yes/no question, drawn by the UI thread and answered by the person sitting there."""

    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > #ask-box {
        width: 78; max-width: 90%; height: auto; padding: 1 2;
        border: thick $warning; background: $surface;
    }
    ConfirmScreen #ask-title { text-style: bold; color: $warning; }
    ConfirmScreen #ask-body { padding: 1 0; color: $accent; }
    ConfirmScreen #ask-why { color: $text-muted; }
    ConfirmScreen #ask-countdown { color: $text-muted; }
    ConfirmScreen #ask-buttons { height: auto; padding-top: 1; }
    ConfirmScreen #ask-buttons Button { margin-right: 2; }
    """
    #: Escape refuses. It is bound explicitly rather than left to a default, because the one answer
    #: this screen must never produce by accident is yes — see `approval.ask`, whose whole rule is
    #: "anything other than an explicit yes is a no".
    BINDINGS = [
        Binding("y", "answer_yes", "Yes", show=True),
        Binding("n", "answer_no", "No", show=True),
        Binding("escape", "answer_no", "No", show=False),
    ]
    #: Focus lands on NO, so a person who answers by hitting Enter without reading has refused —
    #: the same fail-safe ``typer.confirm(default=False)`` carries on the REPL path.
    #:
    #: Declared rather than done in ``on_mount``, and that is a bug fix rather than a style: a
    #: screen's ``on_mount`` can fire *before* its composed children are mounted, so focusing by
    #: query there raises ``NoMatches`` — intermittently, which is the worst way for a default-deny
    #: to be wrong. ``Screen._compose`` applies this after the children exist.
    AUTO_FOCUS = "#ask-no"

    def __init__(self, title: str, body: str, why: str, *, timeout: float) -> None:
        super().__init__()
        self._title = title
        self._body = body
        self._why = why
        self._timeout = timeout
        self._left = int(timeout)

    def compose(self) -> ComposeResult:
        with Vertical(id="ask-box"):
            yield Label(self._title, id="ask-title")
            # `markup=False` rather than `escape()`: this text is the model's own words (a shell
            # command it chose, a tool call a gate stopped), and a surface that renders it as markup
            # is the defect `render.py` exists to prevent — `[/]` in a reply used to raise
            # `MarkupError` and kill the REPL after the turn had been paid for. Not parsing it at
            # all is the version of that fix which cannot be undone by a later edit.
            yield Static(self._body, id="ask-body", markup=False)
            if self._why:
                yield Static(self._why, id="ask-why", markup=False)
            # The clock is rendered by `compose`, not left for the first tick. Measured in the pty
            # run of 2026-09-09: the harness answered 30 ms after the dialog appeared, and the
            # countdown row was still blank in every frame, because a timer that starts on mount
            # has not fired yet and `call_after_refresh` had not landed either. Blank is the one
            # thing this row must never be — it exists to say a refusal is coming.
            yield Static(self._countdown(), id="ask-countdown")
            with Horizontal(id="ask-buttons"):
                yield Button("No  (n)", variant="primary", id="ask-no")
                yield Button("Yes  (y)", variant="warning", id="ask-yes")

    def _countdown(self) -> str:
        return f"no answer in {self._left}s = no"

    def on_mount(self) -> None:
        self.set_interval(1.0, self._tick)

    def _tick(self) -> None:
        """Count the refusal down in public.

        The 120 s that used to elapse here elapsed invisibly; what the person eventually saw was a
        cross with no reason. A timeout that is legible while it runs is a different thing from the
        same timeout reported afterwards, and this is the whole difference between the two.

        A tick before the children are mounted counts nothing, deliberately: the clock is a promise
        about how long the question is *on screen*, and starting it against a screen nobody can see
        yet would be the small version of the failure this whole module replaces.
        """
        rows = list(self.query("#ask-countdown").results(Static))
        if not rows:
            return
        self._left -= 1
        if self._left <= 0:
            self.dismiss(TIMED_OUT)
            return
        rows[0].update(self._countdown())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(YES if event.button.id == "ask-yes" else NO)

    def action_answer_yes(self) -> None:
        self.dismiss(YES)

    def action_answer_no(self) -> None:
        self.dismiss(NO)


class ModalGate:
    """Both of the TUI's gates, answered by one modal. Bound to the app after the app exists.

    Late-bound on purpose, and not for tidiness: the registry is assembled before ``ChimeraTUI`` is
    constructed — tools, then agent, then session, then app — so the thing that will draw the
    question does not exist when the thing that will ask it is built. :class:`ApprovalAnnouncer`
    holds the same slot for the desktop for the same reason.

    The two gates are **different callables with different signatures**, and routing only one of
    them here would leave the measured two-minute block on the other:

    * :meth:`host_exec` has the :data:`~chimera.sandbox.confirm.HostExecConfirm` shape — it is
      handed the command and returns whether to run it. It is reached through
      ``resolve_host_exec_confirm(..., ask=...)``, so ``deny``, ``allow`` and the read-only
      shortcut all keep deciding first, exactly as they do for `chimera chat`.
    * :meth:`question` has the shape ``approval.ask_via`` wants — ``(action, reason)`` — and is
      reached through ``approver_for(..., ask_with=...)``, so ``CHIMERA_APPROVAL_MODE`` still wins.
    """

    def __init__(self, *, timeout: float | None = None) -> None:
        from chimera.sandbox.confirm import PROMPT_TIMEOUT_SECONDS

        #: One number for both surfaces. A modal that is actually drawn could justify a shorter
        #: wait, but two timeouts is how the REPL and the TUI end up disagreeing about what silence
        #: means, and the constant's own docstring already argues for being generous with somebody
        #: who is reading a command they were asked to approve.
        self.timeout = PROMPT_TIMEOUT_SECONDS if timeout is None else timeout
        self._app: App[Any] | None = None
        self._note: Callable[[str], None] | None = None
        self._lock = threading.Lock()
        #: Questions this gate could not put on a screen. Reported rather than inferred: a run that
        #: refused because nobody could be asked is not a run that refused.
        self.unanswerable = 0

    # -- binding -----------------------------------------------------------
    def bind(self, app: App[Any], *, note: Callable[[str], None] | None = None) -> None:
        """Attach the running app. ``note`` writes one dim line into the conversation log."""
        with self._lock:
            self._app = app
            self._note = note

    def release(self) -> None:
        """Detach on the way out, so a question during shutdown refuses instead of hanging."""
        with self._lock:
            self._app = None
            self._note = None

    @property
    def attended(self) -> bool:
        """Whether a question asked right now could reach a person."""
        with self._lock:
            return self._app is not None

    # -- the two gates -----------------------------------------------------
    def host_exec(self, command: str) -> bool:
        """The host-execution gate: run this command on the person's machine, or not.

        The wording is `chimera/sandbox/confirm.py:_prompt`'s, deliberately. Two surfaces asking the
        same question in two different sentences is how a person learns that the second one is a
        different question. The parenthetical moves to its own line rather than being dropped: on
        the REPL that sentence has a whole terminal to sit on, here it has a dialog, and the phrase
        the pty harness watches for must not be split by a wrap.
        """
        return self._ask(
            "⚠  The agent wants to run this on your machine",
            command,
            "(host, not a sandbox)",
        )

    def question(self, action: str, reason: str) -> bool:
        """The governance gate: the taint ledger (or the kernel) wants this call reviewed."""
        return self._ask(
            "⚠  Governance — this call needs your approval",
            action or reason or "review required",
            reason if action else "",
        )

    # -- the one mechanism -------------------------------------------------
    def _ask(self, title: str, body: str, why: str) -> bool:
        with self._lock:
            app, note = self._app, self._note
        if app is None:
            self.unanswerable += 1
            _log.warning("%s: %s", NO_SCREEN, body[:200])
            return False
        try:
            answer = str(
                app.call_from_thread(
                    app.push_screen_wait, ConfirmScreen(title, body, why, timeout=self.timeout)
                )
            )
        except Exception:  # noqa: BLE001 — a screen that cannot be drawn is a refusal, never a crash
            self.unanswerable += 1
            _log.warning("the TUI could not draw its question; refusing: %s", body[:200])
            return False
        if answer == TIMED_OUT and note is not None:
            # Written from the UI thread, because `note` touches a widget and this runs on the
            # worker. Suppressed on failure: the refusal already stands and this line is the
            # explanation for it, not the mechanism.
            with contextlib.suppress(Exception):
                app.call_from_thread(
                    note, f"[yellow]refused — no answer in {int(self.timeout)}s[/yellow]"
                )
        return answer == YES
