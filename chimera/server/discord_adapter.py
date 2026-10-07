"""Discord adapter — make Chimera a native Discord bot.

Receives channel messages, routes each channel to its own :class:`ChatSession` (via the
gateway's ``on_message``), and replies in-channel. It also registers as a
:class:`~chimera.integrations.messaging.MessageSender`, so the agent can send Discord
messages through the ``send_message`` tool.

``discord.py`` is an optional dependency (the ``messaging`` extra); the import is lazy so
core installs stay light. The message-filtering and :class:`InboundMessage` construction
live in the pure :meth:`DiscordAdapter._inbound`, testable without the library or network.
The bot token is read from the environment by the caller — never hard-coded.

Files (study 29, P6.3): with ``attach_files`` the bot sends the deliverables its turn wrote beside
the text — what :mod:`chimera.server.attachments` chose, re-checked here just before sending. Off
unless the caller passes it, and refused here too when the bot has no allowlist, so a caller that
forgets the rule cannot make an open bot hand files to strangers.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
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
        attach_files: bool = False,
        workspace: Path | None = None,
        inbound_media: bool = False,
    ) -> None:
        self.token = token
        self.allowed_users = allowed_users  # None = anyone; else an allowlist of user ids
        self.pairing_flow: Any = None
        self.respond_to_bots = respond_to_bots
        self.inbound_media = inbound_media
        self._media_downloader: Callable[[str], bytes] | None = None
        self.max_chars = min(max_chars, _DISCORD_LIMIT)
        self.attach_files = False
        self.workspace: Path | None = None
        if attach_files:
            self.enable_attachments(workspace)
        self._client: Any = None

    def enable_attachments(self, workspace: Path | None) -> bool:
        """Turn attachments on, checked against ``workspace`` — unless this bot is open.

        Refused here as well as where the setting is read: an open bot never attaches, whoever built
        it and whatever they passed, and with no workspace there is nothing to check a file against.
        """
        if self.allowed_users is None or workspace is None:
            _log.warning(
                "discord: attachments refused — the bot has no allowlist (or no workspace), and an "
                "open bot would send the owner's files to anyone who asks for one"
            )
            return False
        self.attach_files = True
        self.workspace = Path(workspace)
        return True

    def _inbound(
        self,
        *,
        author_id: object,
        author_is_bot: bool,
        is_self: bool,
        channel_id: object,
        content: str,
        attachments: Sequence[Any] = (),
    ) -> InboundMessage | None:
        """Decide whether to handle a message and build the InboundMessage (pure)."""
        if is_self:
            return None  # never react to our own messages (loop guard)
        if author_is_bot and not self.respond_to_bots:
            return None
        if self.pairing_flow is not None and str(author_id) not in self.pairing_flow.allowed_users:
            self.pairing_flow.authorize(str(author_id), content)
            return None
        if self.allowed_users is not None and str(author_id) not in self.allowed_users:
            # Dropped without a reply: answering "you are not allowed" would confirm to a stranger
            # that a bot is listening here. Debug, with the id, so the owner can find their own id
            # when they lock themselves out.
            _log.debug("discord: ignored a message from %s (not in the allowlist)", author_id)
            return None
        text = content.strip()
        media = next((item for item in attachments if Path(str(getattr(item, "filename", ""))).suffix.lower()
                      in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ogg", ".mp3", ".wav", ".m4a", ".webm", ".flac"}), None)
        kind = "audio" if media is not None and Path(str(media.filename)).suffix.lower() in {
            ".ogg", ".mp3", ".wav", ".m4a", ".webm", ".flac"
        } else "image" if media is not None else ""
        if not text and media is None:
            return None
        if media is None and attachments:
            return InboundMessage(
                text=text, chat_id=str(channel_id), platform=self.platform, user=str(author_id),
                from_bot=author_is_bot, media_refusal=True,
            )
        refused = bool(media and not self.inbound_media)
        if refused:
            kind = ""
        return InboundMessage(
            text=text, chat_id=str(channel_id), platform=self.platform, user=str(author_id),
            from_bot=author_is_bot, media_kind=kind,
            media_name=str(getattr(media, "filename", "")) if kind else "",
            media_data=None, media_file_id=str(getattr(media, "url", "")) if kind else "",
            media_refusal=refused,
        )

    async def _respond(
        self,
        inbound: InboundMessage,
        route: Callable[[InboundMessage], str],
        *,
        typing: Callable[[], Any],
        send: Callable[[str], Any],
        send_files: Callable[[list[Path]], Any] | None = None,
    ) -> None:
        """Run the (sync) agent off the event loop under a typing indicator, then send the reply.

        The typing indicator ("Chimera is typing…") shows the message was received and a turn is in
        flight — the caller passes ``message.channel.typing`` (an async context manager) and
        ``message.channel.send`` (a coroutine). Kept free of discord.py so it's testable with fakes.

        ``send_files`` posts the turn's attachments after the text, when this adapter attaches and the
        reply names any (:class:`~chimera.server.gateway.Reply`).
        """
        import asyncio

        loop = asyncio.get_running_loop()
        async with typing():  # discord auto-refreshes the indicator until the turn returns
            reply = await loop.run_in_executor(None, route, inbound)
        for chunk in chunk_text(reply, self.max_chars) or ["(no reply)"]:
            await send(chunk)
        files = self.files_to_send(getattr(reply, "files", ()))
        if files and send_files is not None:
            try:
                await send_files(files)
            except Exception as exc:  # noqa: BLE001 — the text already went; say why the file did not
                _log.warning("discord: attaching %d file(s) failed: %s", len(files), exc)
                await send(f"⚠ the file(s) could not be attached: {type(exc).__name__}")

    def files_to_send(self, files: Sequence[Path]) -> list[Path]:
        """The reply's files this adapter will actually send — none unless it attaches, each re-checked.

        Checked again here and not only when the turn's list was built: the adapter is the last code
        before the bytes leave, a reply could have been built by a path that never asked, and the file
        itself may have changed between the two (grown past the limit, been replaced by another type).
        """
        if not self.attach_files or self.workspace is None:
            return []
        from chimera.server.attachments import MAX_ATTACHMENTS, check_attachment

        kept: list[Path] = []
        for path in files:
            reason = check_attachment(Path(path), self.workspace)
            if reason is not None:
                _log.info("discord: not attaching %s: %s", Path(path).name, reason)
                continue
            kept.append(Path(path))
        return kept[:MAX_ATTACHMENTS]

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
                attachments=message.attachments,
            )
            if inbound is None:
                return
            if inbound.media_kind and inbound.media_file_id:
                try:
                    if self._media_downloader is not None:
                        inbound.media_data = self._media_downloader(inbound.media_file_id)
                    else:
                        inbound.media_data = await message.attachments[
                            next(i for i, item in enumerate(message.attachments)
                                 if str(getattr(item, "url", "")) == inbound.media_file_id)
                        ].read()
                except Exception as exc:
                    _log.warning("discord media download failed: %s", exc)
                    inbound.media_kind = ""
                    inbound.media_refusal = True
            # Show a typing indicator while the (synchronous) agent runs off the event loop, so a
            # slow turn does not block the gateway and the user sees it was received and is working.
            async def send_files(paths: list[Path]) -> None:
                await message.channel.send(
                    files=[discord.File(str(p), filename=p.name) for p in paths]
                )

            await self._respond(
                inbound, route, typing=message.channel.typing, send=message.channel.send,
                send_files=send_files if self.attach_files else None,
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
