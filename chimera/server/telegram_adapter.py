"""Telegram adapter — make Chimera a native Telegram bot.

Same shape as the Discord adapter (an :class:`~chimera.server.gateway.Adapter` that
receives + replies, one session per chat, plus a
:class:`~chimera.integrations.messaging.MessageSender` so the agent can send). Telegram's
Bot API is plain HTTP (long-poll ``getUpdates`` + ``sendMessage``), so this needs **no
extra dependency** — just ``httpx`` (already core). Update parsing + filtering live in the
pure :meth:`TelegramAdapter._message_from_update`, testable without a network. The bot
token is read from the environment by the caller — never hard-coded.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from typing import Any

from chimera.server.gateway import InboundMessage, chunk_text, run_with_indicator
from chimera.telemetry import get_logger

_log = get_logger("server.telegram")
_TELEGRAM_LIMIT = 4096
_API = "https://api.telegram.org"


class TelegramAdapter:
    """A platform transport + sender for Telegram (long-polling Bot API)."""

    name = "telegram"
    platform = "telegram"

    def __init__(
        self,
        token: str,
        *,
        allowed_users: set[str] | None = None,
        respond_to_bots: bool = False,
        poll_timeout: int = 30,
        max_chars: int = _TELEGRAM_LIMIT,
        inbound_media: bool = False,
    ) -> None:
        self.token = token
        self.allowed_users = allowed_users  # None = anyone; else an allowlist of user ids
        self.pairing_flow: Any = None
        self.respond_to_bots = respond_to_bots
        self.poll_timeout = poll_timeout
        self.max_chars = min(max_chars, _TELEGRAM_LIMIT)
        self.inbound_media = inbound_media
        self._media_downloader: Callable[[str], bytes] | None = None
        self._running = False

    def _message_from_update(self, update: dict[str, Any]) -> InboundMessage | None:
        """Filter + build an InboundMessage from one Telegram update (pure)."""
        message = update.get("message") or update.get("edited_message")
        if not isinstance(message, dict):
            return None
        sender = message.get("from") or {}
        if sender.get("is_bot") and not self.respond_to_bots:
            return None
        user_id = str(sender.get("id", ""))
        if self.pairing_flow is not None and user_id not in self.pairing_flow.allowed_users:
            self.pairing_flow.authorize(user_id, str(message.get("text") or ""))
            return None
        if self.allowed_users is not None and user_id not in self.allowed_users:
            # No reply, for the reason in the Discord adapter; the id is logged for the owner.
            _log.debug("telegram: ignored a message from %s (not in the allowlist)", user_id)
            return None
        text = str(message.get("text") or message.get("caption") or "").strip()
        media_kind = ""
        media_file_id = ""
        media_name = ""
        if message.get("voice") or message.get("audio"):
            media_kind = "audio"
            audio = message.get("voice") or message.get("audio") or {}
            media_file_id = str(audio.get("file_id", ""))
            media_name = str(audio.get("file_name") or "voice.ogg")
        elif message.get("photo"):
            media_kind = "image"
            photo = message["photo"][-1]
            media_file_id = str(photo.get("file_id", ""))
            media_name = "photo.jpg"
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if not chat_id or (not text and not media_file_id):
            return None
        refused = bool(media_kind and not self.inbound_media)
        if refused:
            media_kind = ""
        return InboundMessage(
            text=text, chat_id=chat_id, platform=self.platform, user=user_id,
            from_bot=bool(sender.get("is_bot")), media_refusal=refused,
            media_kind=media_kind, media_file_id=media_file_id, media_name=media_name,
        )

    def _url(self, method: str) -> str:
        return f"{_API}/bot{self.token}/{method}"

    def start(self, route: Callable[[InboundMessage], str]) -> None:
        """Long-poll for updates and reply, until :meth:`stop` (blocking)."""
        import httpx

        self._running = True
        offset = 0
        with httpx.Client(timeout=self.poll_timeout + 10) as client:
            while self._running:
                try:
                    resp = client.get(
                        self._url("getUpdates"),
                        params={"offset": offset, "timeout": self.poll_timeout},
                    )
                    updates = resp.json().get("result", [])
                except (httpx.HTTPError, ValueError) as exc:  # network or bad JSON
                    _log.warning("telegram poll failed: %s", exc)
                    time.sleep(3)
                    continue
                for update in updates:
                    offset = max(offset, int(update.get("update_id", 0)) + 1)
                    # Show "typing…" while the (blocking) turn runs — Telegram's chat action expires
                    # after ~5s, so re-send it periodically until the reply is ready.
                    try:
                        inbound = self._message_from_update(update)
                        if inbound is None:
                            continue
                        self._attach_media(client, inbound)
                        reply = run_with_indicator(
                            route, inbound, ping=partial(self._typing, client, inbound.chat_id)
                        )
                        self._post(client, inbound.chat_id, reply)
                    except Exception as exc:  # one bad turn or send must not stop polling
                        # Redacted: an httpx error's message carries the request URL, and every
                        # Bot API URL has the token in its path.
                        detail = str(exc).replace(self.token, "<token>") if self.token else str(exc)
                        _log.warning("telegram update %s failed: %s", update.get("update_id"), detail)

    def _attach_media(self, client: Any, inbound: InboundMessage) -> None:
        """Download an attached voice note or photo; on failure, answer as if media were off."""
        import httpx

        if not (inbound.media_kind and inbound.media_file_id):
            return
        try:
            if self._media_downloader is not None:
                inbound.media_data = self._media_downloader(inbound.media_file_id)
            else:
                info = client.get(
                    self._url("getFile"), params={"file_id": inbound.media_file_id}
                ).json()["result"]
                media_resp = client.get(
                    f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
                )
                media_resp.raise_for_status()
                inbound.media_data = media_resp.content
        except (KeyError, ValueError, httpx.HTTPError) as exc:
            # The type, never the text: an httpx error's message carries the request URL, and a
            # file URL has the bot token in its path.
            _log.warning("telegram media download failed: %s", type(exc).__name__)
            inbound.media_refusal = True
            inbound.media_kind = ""

    def _typing(self, client: Any, chat_id: str) -> None:
        """Send the 'typing' chat action (best-effort; expires ~5s, refreshed by run_with_indicator)."""
        client.post(self._url("sendChatAction"), json={"chat_id": chat_id, "action": "typing"})

    def _post(self, client: Any, chat_id: str, text: str) -> int:
        sent = 0
        for chunk in chunk_text(text, self.max_chars):
            client.post(self._url("sendMessage"), json={"chat_id": chat_id, "text": chunk})
            sent += 1
        return sent

    def stop(self) -> None:
        self._running = False

    def send(self, chat_id: str, text: str) -> str:
        """MessageSender: post to a chat (agent tool). Synchronous HTTP — works anywhere."""
        import httpx

        try:
            with httpx.Client(timeout=30) as client:
                count = self._post(client, chat_id, text)
        except httpx.HTTPError as exc:  # a platform error must not crash the agent loop
            return f"error: telegram send failed: {exc}"
        return f"sent {count} message(s) to telegram chat {chat_id}"
