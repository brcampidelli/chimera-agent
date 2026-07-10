"""Discord adapter — make Chimera a native Discord bot.

Receives channel messages, routes each channel to its own :class:`ChatSession` (via the
gateway's ``on_message``), and replies in-channel. It also registers as a
:class:`~chimera.integrations.messaging.MessageSender`, so the agent can send Discord
messages through the ``send_message`` tool.

``discord.py`` is an optional dependency (the ``messaging`` extra); the import is lazy so
core installs stay light. The message-filtering and :class:`InboundMessage` construction
live in the pure :meth:`DiscordAdapter._inbound`, testable without the library or network.
The bot token is read from the environment by the caller — never hard-coded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from chimera.server.gateway import InboundMessage, chunk_text
from chimera.telemetry import get_logger

_log = get_logger("server.discord")
_DISCORD_LIMIT = 2000


class DiscordAdapter:
    """A platform transport + sender for Discord."""

    name = "discord"
    platform = "discord"

    def __init__(
        self,
        token: str,
        *,
        allowed_users: set[str] | None = None,
        respond_to_bots: bool = False,
        max_chars: int = _DISCORD_LIMIT,
    ) -> None:
        self.token = token
        self.allowed_users = allowed_users  # None = anyone; else an allowlist of user ids
        self.respond_to_bots = respond_to_bots
        self.max_chars = min(max_chars, _DISCORD_LIMIT)
        self._client: Any = None

    def _inbound(
        self,
        *,
        author_id: object,
        author_is_bot: bool,
        is_self: bool,
        channel_id: object,
        content: str,
    ) -> InboundMessage | None:
        """Decide whether to handle a message and build the InboundMessage (pure)."""
        if is_self:
            return None  # never react to our own messages (loop guard)
        if author_is_bot and not self.respond_to_bots:
            return None
        if self.allowed_users is not None and str(author_id) not in self.allowed_users:
            return None
        text = content.strip()
        if not text:
            return None
        return InboundMessage(
            text=text, chat_id=str(channel_id), platform=self.platform, user=str(author_id)
        )

    async def _respond(
        self,
        inbound: InboundMessage,
        route: Callable[[InboundMessage], str],
        *,
        typing: Callable[[], Any],
        send: Callable[[str], Any],
    ) -> None:
        """Run the (sync) agent off the event loop under a typing indicator, then send the reply.

        The typing indicator ("Chimera is typing…") shows the message was received and a turn is in
        flight — the caller passes ``message.channel.typing`` (an async context manager) and
        ``message.channel.send`` (a coroutine). Kept free of discord.py so it's testable with fakes.
        """
        import asyncio

        loop = asyncio.get_running_loop()
        async with typing():  # discord auto-refreshes the indicator until the turn returns
            reply = await loop.run_in_executor(None, route, inbound)
        for chunk in chunk_text(reply, self.max_chars) or ["(no reply)"]:
            await send(chunk)

    def start(self, route: Callable[[InboundMessage], str]) -> None:
        """Connect the bot and serve until interrupted (blocking).

        ``route`` is the gateway's ``on_message`` — the discord.py handler must keep the
        name ``on_message`` for the library to dispatch to it.
        """
        import discord  # lazy, optional dependency (the `messaging` extra)

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event  # type: ignore[misc,untyped-decorator]  # discord.py is untyped
        async def on_ready() -> None:
            _log.info("Discord adapter online as %s", client.user)

        @client.event  # type: ignore[misc,untyped-decorator]  # discord.py is untyped
        async def on_message(message: Any) -> None:
            inbound = self._inbound(
                author_id=message.author.id,
                author_is_bot=bool(message.author.bot),
                is_self=message.author == client.user,
                channel_id=message.channel.id,
                content=message.content or "",
            )
            if inbound is None:
                return
            # Show a typing indicator while the (synchronous) agent runs off the event loop, so a
            # slow turn does not block the gateway and the user sees it was received and is working.
            await self._respond(
                inbound, route, typing=message.channel.typing, send=message.channel.send
            )

        client.run(self.token)

    def stop(self) -> None:
        client = self._client
        if client is None:
            return
        import asyncio

        loop = getattr(client, "loop", None)
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(client.close(), loop)

    def send(self, chat_id: str, text: str) -> str:
        """MessageSender: post to a channel from outside the event loop (agent tool)."""
        import asyncio

        client = self._client
        loop = getattr(client, "loop", None) if client is not None else None
        if client is None or loop is None or not loop.is_running():
            return "error: discord adapter is not running"

        async def _deliver() -> int:
            channel = client.get_channel(int(chat_id)) or await client.fetch_channel(int(chat_id))
            sent = 0
            for chunk in chunk_text(text, self.max_chars) or ["(no reply)"]:
                await channel.send(chunk)
                sent += 1
            return sent

        try:
            count = asyncio.run_coroutine_threadsafe(_deliver(), loop).result(timeout=30)
        except Exception as exc:  # noqa: BLE001 - surface send failures as a tool result
            return f"error: discord send failed: {exc}"
        return f"sent {count} message(s) to discord channel {chat_id}"
