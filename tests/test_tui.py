"""Tests for the Textual TUI.

Dispatch logic is tested without an event loop; a headless mount + interactive-flow smoke confirms the
widget tree, the streaming/tool callbacks, the activity panel, and the key bindings — all offline.
"""

from __future__ import annotations

from collections.abc import Callable

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
        assert "cache r/w 5/0" in tokens_txt
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
