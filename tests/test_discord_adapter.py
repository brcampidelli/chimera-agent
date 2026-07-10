"""Tests for the Discord adapter's pure logic (no discord.py, no network)."""

from __future__ import annotations

import asyncio

from chimera.server import DiscordAdapter, chunk_text
from chimera.server.gateway import InboundMessage


def test_inbound_ignores_self_and_empty() -> None:
    adapter = DiscordAdapter("token")
    assert adapter._inbound(author_id=1, author_is_bot=False, is_self=True, channel_id=9, content="hi") is None
    assert adapter._inbound(author_id=1, author_is_bot=False, is_self=False, channel_id=9, content="   ") is None


def test_inbound_ignores_bots_unless_allowed() -> None:
    assert (
        DiscordAdapter("token")._inbound(
            author_id=2, author_is_bot=True, is_self=False, channel_id=9, content="hi"
        )
        is None
    )
    allowed = DiscordAdapter("token", respond_to_bots=True)._inbound(
        author_id=2, author_is_bot=True, is_self=False, channel_id=9, content="hi"
    )
    assert allowed is not None and allowed.platform == "discord"


def test_inbound_respects_allowlist() -> None:
    adapter = DiscordAdapter("token", allowed_users={"42"})
    assert adapter._inbound(author_id=7, author_is_bot=False, is_self=False, channel_id=9, content="hi") is None
    msg = adapter._inbound(author_id=42, author_is_bot=False, is_self=False, channel_id=9, content="hi")
    assert msg is not None and msg.chat_id == "9" and msg.user == "42"


def test_inbound_builds_trimmed_message() -> None:
    msg = DiscordAdapter("token")._inbound(
        author_id=1, author_is_bot=False, is_self=False, channel_id=555, content="  hello  "
    )
    assert msg is not None and msg.text == "hello" and msg.chat_id == "555" and msg.platform == "discord"


def test_send_without_running_client_errors() -> None:
    assert DiscordAdapter("token").send("1", "hi").startswith("error:")


def test_chunk_text_splits_and_is_empty_for_empty() -> None:
    parts = chunk_text("a" * 4500, 2000)
    assert len(parts) == 3 and all(len(p) <= 2000 for p in parts)
    assert chunk_text("", 2000) == []


class _FakeTyping:
    """Stand-in for discord's channel.typing() async context manager, recording enter/exit."""

    def __init__(self, log: list[str]) -> None:
        self._log = log

    async def __aenter__(self) -> None:
        self._log.append("typing:start")

    async def __aexit__(self, *exc: object) -> None:
        self._log.append("typing:stop")


def test_respond_shows_typing_around_the_turn_then_sends() -> None:
    events: list[str] = []
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    def route(_msg: InboundMessage) -> str:
        events.append("route")
        return "a" * 4500  # 3 chunks at 2000

    adapter = DiscordAdapter("token", max_chars=2000)
    inbound = InboundMessage(text="hi", chat_id="9", platform="discord", user="1")
    asyncio.run(
        adapter._respond(inbound, route, typing=lambda: _FakeTyping(events), send=send)
    )
    # The typing indicator brackets the agent turn; the reply is chunked afterwards.
    assert events == ["typing:start", "route", "typing:stop"]
    assert len(sent) == 3 and all(len(s) <= 2000 for s in sent)


def test_respond_sends_placeholder_for_empty_reply() -> None:
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)

    adapter = DiscordAdapter("token")
    inbound = InboundMessage(text="hi", chat_id="9", platform="discord", user="1")
    asyncio.run(
        adapter._respond(inbound, lambda _m: "", typing=lambda: _FakeTyping([]), send=send)
    )
    assert sent == ["(no reply)"]
