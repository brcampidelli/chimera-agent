"""Typing / working indicators across the messaging adapters (no network).

Covers the shared refresh helper (`run_with_indicator`) and each adapter's platform-specific signal:
Telegram chat action, Signal typing indicator, Slack ⏳ reaction (Slack has no bot typing).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from chimera.server import SignalAdapter, SlackAdapter, TelegramAdapter
from chimera.server.gateway import InboundMessage, run_with_indicator

# --- shared helper ----------------------------------------------------------------------


def test_run_with_indicator_pings_and_returns_reply() -> None:
    pings: list[int] = []
    reply = run_with_indicator(
        lambda _m: "answer", InboundMessage(text="hi"), ping=lambda: pings.append(1)
    )
    assert reply == "answer"
    assert len(pings) >= 1  # pinged immediately, before the turn


def test_run_with_indicator_swallows_ping_errors() -> None:
    def bad_ping() -> None:
        raise RuntimeError("boom")

    assert run_with_indicator(lambda _m: "ok", InboundMessage(text="hi"), ping=bad_ping) == "ok"


def test_run_with_indicator_empty_reply_gets_placeholder() -> None:
    assert run_with_indicator(lambda _m: "", InboundMessage(text="hi"), ping=lambda: None) == "(no reply)"


# --- Telegram: sendChatAction("typing") -------------------------------------------------


def test_telegram_typing_sends_chat_action() -> None:
    calls: list[tuple[str, Any]] = []

    class FakeClient:
        def post(self, url: str, json: Any = None) -> None:
            calls.append((url, json))

    TelegramAdapter("tok")._typing(FakeClient(), "42")
    assert len(calls) == 1
    url, body = calls[0]
    assert url.endswith("/sendChatAction")
    assert body == {"chat_id": "42", "action": "typing"}


# --- Signal: typing-indicator PUT/DELETE ------------------------------------------------


def test_signal_typing_puts_then_deletes() -> None:
    calls: list[tuple[str, str, Any]] = []

    class FakeClient:
        def request(self, method: str, url: str, json: Any = None) -> None:
            calls.append((method, url, json))

    adapter = SignalAdapter("http://bridge", "+100")
    client = FakeClient()
    adapter._typing(client, "+200", on=True)
    adapter._typing(client, "+200", on=False)
    assert [c[0] for c in calls] == ["PUT", "DELETE"]
    assert calls[0][1].endswith("/v1/typing-indicator/+100")
    assert calls[0][2] == {"recipient": "+200"}


def test_signal_typing_is_best_effort() -> None:
    class BoomClient:
        def request(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("bridge down")

    # Must not raise — typing is best-effort.
    SignalAdapter("http://bridge", "+100")._typing(BoomClient(), "+200", on=True)


# --- Slack: ⏳ reaction (no native typing) ----------------------------------------------


def test_slack_react_add_and_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[tuple[str, Any]] = []

    class FakeClient:
        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

        def post(self, url: str, headers: Any = None, json: Any = None) -> None:
            posts.append((url, json))

    monkeypatch.setattr(httpx, "Client", lambda *a, **k: FakeClient())
    adapter = SlackAdapter("xoxb", "xapp")
    adapter._react("C1", "123.45", add=True)
    adapter._react("C1", "123.45", add=False)
    assert posts[0][0].endswith("reactions.add")
    assert posts[1][0].endswith("reactions.remove")
    assert posts[0][1] == {"channel": "C1", "timestamp": "123.45", "name": "hourglass_flowing_sand"}


def test_slack_react_noop_without_ts(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> None:
        raise AssertionError("must not open a client without a message ts")

    monkeypatch.setattr(httpx, "Client", boom)
    SlackAdapter("xoxb", "xapp")._react("C1", "", add=True)  # no raise = pass
