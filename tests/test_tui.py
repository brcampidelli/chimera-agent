"""Tests for the Textual TUI.

Dispatch logic is tested without an event loop; a headless mount smoke confirms
the widget tree comes up.
"""

from __future__ import annotations

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

    app = ChimeraTUI(FakeSession(), model_label="test-model")
    async with app.run_test():
        assert app.query_one("#log", RichLog) is not None
        assert app.query_one("#prompt", Input) is not None
        assert app.sub_title == "test-model"
