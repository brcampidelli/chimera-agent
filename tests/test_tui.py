"""Tests for the Textual TUI.

Dispatch logic is tested without an event loop; a headless mount + interactive-flow smoke confirms the
widget tree, the streaming/tool callbacks, the activity panel, and the key bindings — all offline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from chimera.api.durable_undo import DurableUndo
from chimera.core.agent import ToolActivity
from chimera.interface.session import TurnReport
from chimera.tui.app import ChimeraTUI


class FakeSession:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.reset_called = False

    def send(self, message: str) -> str:
        self.sent.append(message)
        return f"echo: {message}"

    def reset(self) -> None:
        self.reset_called = True


def test_reply_to_sends_a_normal_message() -> None:
    fake = FakeSession()
    app = ChimeraTUI(fake)  # constructed, not run
    assert app.reply_to("hello") == "echo: hello"
    assert fake.sent == ["hello"]


def test_reply_to_reset_returns_none_and_resets() -> None:
    fake = FakeSession()
    app = ChimeraTUI(fake)
    assert app.reply_to("/reset") is None
    assert fake.reset_called is True


async def test_tui_mounts_with_expected_widgets() -> None:
    from textual.widgets import Input, RichLog

    from chimera.tui.activity import ActivityPanel

    app = ChimeraTUI(FakeSession(), model_label="test-model")
    async with app.run_test():
        assert app.query_one("#log", RichLog) is not None
        assert app.query_one("#prompt", Input) is not None
        assert app.query_one("#activity", ActivityPanel) is not None
        assert app.query_one("#prompt", Input).suggester is not None  # slash-command autocomplete
        assert app.sub_title == "test-model"


class DrivenSession:
    """Session that records the interactive flow and drives the streaming/tool callbacks."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.model: str | None = "stub"
        self.reset_called = False
        self.tokens_streamed = 0

    def send_verbose(
        self,
        message: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> TurnReport:
        self.sent.append(message)
        if on_token is not None:
            for piece in ("Hel", "lo"):  # simulate a token stream
                self.tokens_streamed += 1
                on_token(piece)
        if on_tool is not None:
            on_tool(ToolActivity("read_file", {"path": "x"}, ok=True, observation="ok"))
        return TurnReport(
            answer="Hello",
            prompt_tokens=12,
            completion_tokens=3,
            cache_read_tokens=5,
            usd=0.0001,
            tool_names=["read_file"],
            memory_facts_used=2,
            memory_layer="keyword",
            steps=1,
            stopped_reason="final",
        )

    def set_model(self, slug: str | None) -> bool:
        self.model = slug
        return True

    def reset(self) -> None:
        self.reset_called = True


async def test_multiline_paste_is_one_user_turn() -> None:
    """One bracketed paste keeps its newlines and is ONE turn, with its text there once.

    Textual dispatches a message to the handler of every class in the MRO, so overriding
    ``Input._on_paste`` without preventing the default let the base class insert the first line a
    second time (measured: ``"first line\\n…third linefirst line"``)."""
    from textual import events
    from textual.widgets import Input

    session = DrivenSession()
    app = ChimeraTUI(session, model_label="stub")
    pasted = "first line\nsecond line\nthird line"
    async with app.run_test() as pilot:
        prompt = app.query_one("#prompt", Input)
        prompt.post_message(events.Paste(pasted))
        await pilot.pause()
        assert prompt.value == pasted
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
    assert session.sent == [pasted]


async def test_tui_interactive_flow_headless() -> None:
    """Drive the real Textual app headless: type → submit → streaming worker → activity panel."""
    from textual.widgets import Input, RichLog, Static

    session = DrivenSession()
    app = ChimeraTUI(session, model_label="stub")
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "hello there"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()

        assert session.sent == ["hello there"]  # routed through the streaming send_verbose
        assert session.tokens_streamed == 2  # the on_token stream fired
        assert app._live == ""  # live buffer cleared after finish
        assert len(app.query_one("#log", RichLog).lines) > 0  # the rendered answer landed

        # The activity panel reflects the real signals from the turn.
        assert "read_file" in str(app.query_one("#act-tools", Static).render())
        tokens_txt = str(app.query_one("#act-tokens", Static).render())
        assert "in 12" in tokens_txt
        # DrivenSession reports cache reads but omits cache writes; showing 0 would turn unknown
        # telemetry into a fabricated measured zero.
        assert "cache r/w 5/–" in tokens_txt
        assert "None" not in tokens_txt
        assert "(excl. cache)" in tokens_txt  # cost honestly flags it excludes cache pricing
        assert app.query_one("#prompt", Input).disabled is False  # input re-enabled after the turn
        assert "2 fact" in str(app.query_one("#act-memory", Static).render())
        assert "keyword" in str(app.query_one("#act-memory", Static).render())  # layer label shown

        # /model switches the model through the real input handler.
        app.query_one("#prompt", Input).value = "/model openrouter/x"
        await pilot.press("enter")
        await pilot.pause()
        assert session.model == "openrouter/x"

        # ^R resets the conversation; ^L clears the transcript.
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert session.reset_called is True
        await pilot.press("ctrl+l")
        await pilot.pause()
        assert len(app.query_one("#log", RichLog).lines) == 0

        app.query_one("#prompt", Input).value = "/exit"
        await pilot.press("enter")
        await pilot.pause()
    assert app.is_running is False


async def test_input_disabled_while_a_turn_is_in_flight() -> None:
    """A second Enter must not start a concurrent turn — the input is disabled until the turn ends."""
    import threading

    from textual.widgets import Input

    class GatedSession(DrivenSession):
        def __init__(self) -> None:
            super().__init__()
            self.release = threading.Event()

        def send_verbose(self, message, *, on_token=None, on_tool=None):  # type: ignore[no-untyped-def]
            self.release.wait(2)  # block the worker until the test releases it
            return super().send_verbose(message, on_token=on_token, on_tool=on_tool)

    session = GatedSession()
    app = ChimeraTUI(session, model_label="stub")
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "hi"
        await pilot.press("enter")
        await pilot.pause()
        assert app.query_one("#prompt", Input).disabled is True  # blocked mid-turn -> input locked
        session.release.set()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.query_one("#prompt", Input).disabled is False  # re-enabled after the turn


async def test_stream_forced_off_under_fuse() -> None:
    """--fuse must not promise a token stream: on_token is never passed, so nothing streams."""
    from textual.widgets import Input

    session = DrivenSession()
    app = ChimeraTUI(session, model_label="stub", stream=True, fuse=True)
    assert app.stream_enabled is False
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert session.sent == ["hi"]
        assert session.tokens_streamed == 0  # no live tokens under fusion


# --- --fuse says what ran --------------------------------------------------------------------
#
# The busy label read "fusion — synthesizing" on every turn under --fuse. Fusion never answers a step
# that can call a tool (`RoutedBackend.complete`), and every step of this agent can, so on almost
# every turn the label promised work that did not happen (bench/PLAN-right-hand.md, step 6).


class _FusedSession(DrivenSession):
    """A turn that DID reach fusion: the answer's model is "fusion"."""

    def send_verbose(
        self,
        message: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> TurnReport:
        report = super().send_verbose(message, on_token=on_token, on_tool=on_tool)
        report.model = "fusion"
        return report


def _log_text(app: ChimeraTUI) -> str:
    from textual.widgets import RichLog

    return "\n".join(str(line.text) for line in app.query_one("#log", RichLog).lines)


def test_the_busy_label_no_longer_claims_fusion_is_synthesizing() -> None:
    label = ChimeraTUI(DrivenSession(), model_label="stub", fuse=True)._busy_label()
    assert "synthesizing" not in label
    assert "without tools" in label


async def test_a_turn_that_went_to_one_model_says_so_once() -> None:
    from textual.widgets import Input

    app = ChimeraTUI(DrivenSession(), model_label="stub", fuse=True)
    async with app.run_test() as pilot:
        for text in ("one", "two"):
            app.query_one("#prompt", Input).value = text
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
        assert _log_text(app).count("this turn went to one model") == 1


async def test_a_turn_that_reached_fusion_is_not_told_otherwise() -> None:
    from textual.widgets import Input

    app = ChimeraTUI(_FusedSession(), model_label="stub", fuse=True)
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "went to one model" not in _log_text(app)


async def test_without_fuse_nothing_is_said_about_fusion() -> None:
    from textual.widgets import Input

    app = ChimeraTUI(DrivenSession(), model_label="stub")
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "hi"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert "--fuse" not in _log_text(app)


class _PlanningSession(DrivenSession):
    """A turn in which the agent kept a task list."""

    def send_verbose(
        self,
        message: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> TurnReport:
        report = super().send_verbose(message, on_token=on_token, on_tool=on_tool)
        report.todos = [("read the test", "done"), ("fix it", "doing")]
        return report


async def test_the_task_list_the_agent_kept_is_drawn_under_the_answer() -> None:
    from textual.widgets import Input

    app = ChimeraTUI(_PlanningSession(), model_label="stub")
    async with app.run_test() as pilot:
        app.query_one("#prompt", Input).value = "fix it"
        await pilot.press("enter")
        await app.workers.wait_for_complete()
        await pilot.pause()
        text = _log_text(app)
        assert "tasks 1/2 done" in text and "fix it" in text


# -- an unrecognised slash is never a paid turn, and `/undo N` works here ---------------------------


class _CountingHand:
    """The `RightHand` seam a turn touches first: every call to it means a turn was about to start."""

    attended = False

    def __init__(self) -> None:
        self.begun: list[str] = []

    def turn_verdicts(self) -> tuple[int, int]:
        return 0, 0

    def begin_turn(self, text: str) -> None:
        self.begun.append(text)

    def taint_if_plugin_command(self, _custom: object, _text: str) -> None:
        return None


async def _submit(app: ChimeraTUI, pilot: object, text: str) -> None:
    from textual.widgets import Input

    app.query_one("#prompt", Input).value = text
    await pilot.press("enter")  # type: ignore[attr-defined]
    await app.workers.wait_for_complete()
    await pilot.pause()  # type: ignore[attr-defined]


async def test_an_unknown_slash_command_is_answered_by_the_app_and_never_reaches_the_model() -> None:
    from chimera.orchestration.budget import SpendBudget

    session, hand, budget = DrivenSession(), _CountingHand(), SpendBudget(max_usd=1.0)
    app = ChimeraTUI(session, model_label="stub", hand=hand, budget=budget)  # type: ignore[arg-type]
    left = budget.remaining
    async with app.run_test() as pilot:
        for typed in ("/foo", "/undoo", "/newsletter"):
            await _submit(app, pilot, typed)
        text = _log_text(app)
    assert "unknown command /foo" in text
    assert "unknown command /undoo" in text
    assert "unknown command /newsletter" in text
    assert "not sent to the model" in text
    assert session.sent == []
    assert hand.begun == []
    assert budget.remaining == left
    assert "you ›" not in text  # not even echoed as if it were a message


async def test_a_path_or_a_fraction_still_reaches_the_model() -> None:
    session, hand = DrivenSession(), _CountingHand()
    app = ChimeraTUI(session, model_label="stub", hand=hand)  # type: ignore[arg-type]
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/usr/local/bin")
        await _submit(app, pilot, "/2")
    assert session.sent == ["/usr/local/bin", "/2"]
    assert hand.begun == ["/usr/local/bin", "/2"]


class _UndoableSession(DrivenSession):
    """A session that keeps what its turns changed, the way `ChatSession` does with a workspace."""

    def __init__(self, workspace: Path, store: DurableUndo) -> None:
        super().__init__()
        self.workspace = workspace
        self.durable_undo = store
        self.turns: list[object] = []
        self._pending_note = ""
        self._last_change = None


def _undo_app(tmp_path: Path) -> tuple[ChimeraTUI, Path, Path, _UndoableSession]:
    """An app over a folder where two turns each changed one file, recorded under the app's own id."""
    from chimera.core.checkpoint import WorkspaceGuard

    root, home = tmp_path / "project", tmp_path / "home"
    root.mkdir()
    first, second = root / "a.txt", root / "b.txt"
    first.write_text("a0", encoding="utf-8")
    second.write_text("b0", encoding="utf-8")
    store = DurableUndo(home)
    session = _UndoableSession(root, store)
    app = ChimeraTUI(session, model_label="stub", usage_home=home)  # type: ignore[arg-type]
    guard = WorkspaceGuard(root)
    for turn_id, path, text in (("00000001", first, "a1"), ("00000002", second, "b1")):
        before = guard.snapshot()
        path.write_text(text, encoding="utf-8")
        store.record(app._usage_id(), turn_id, root, guard.diff_since(before))
    return app, first, second, session


async def test_undo_n_in_the_tui_previews_asks_and_restores_only_on_yes(tmp_path: Path) -> None:
    from chimera.tui.confirm import ConfirmScreen

    app, a, b, session = _undo_app(tmp_path)
    async with app.run_test() as pilot:
        await _submit(app, pilot, "/undo 2")
        assert isinstance(app.screen, ConfirmScreen)
        text = _log_text(app)
        assert "will undo the last 2 turn(s)" in text and "a.txt" in text and "b.txt" in text
        assert (a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")) == ("a1", "b1")
        await pilot.press("n")  # a no restores nothing
        await pilot.pause()
        assert (a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")) == ("a1", "b1")
        assert "undo cancelled" in _log_text(app)

        await _submit(app, pilot, "/undo 2")
        assert isinstance(app.screen, ConfirmScreen)
        await pilot.press("y")
        await pilot.pause()
        assert (a.read_text(encoding="utf-8"), b.read_text(encoding="utf-8")) == ("a0", "b0")
        assert "restored 2 file(s)" in _log_text(app)
    assert session.sent == []  # neither undo reached the model
    assert "b.txt" in session._pending_note


async def test_undo_with_a_bad_count_asks_nothing_and_never_calls_the_model(
    tmp_path: Path,
) -> None:
    from chimera.tui.confirm import ConfirmScreen

    app, first, _second, session = _undo_app(tmp_path)
    async with app.run_test() as pilot:
        for bad in ("/undo 0", "/undo x", "/undo -1"):
            await _submit(app, pilot, bad)
            assert not isinstance(app.screen, ConfirmScreen)
        assert _log_text(app).count("usage: /undo [N]") == 3
    assert first.read_text(encoding="utf-8") == "a1"
    assert session.sent == []
