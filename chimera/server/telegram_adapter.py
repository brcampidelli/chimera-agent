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
from typing import Any

from chimera.server.gateway import InboundMessage, chunk_text
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
    ) -> None:
        self.token = token
        self.allowed_users = allowed_users  # None = anyone; else an allowlist of user ids
        self.respond_to_bots = respond_to_bots
        self.poll_timeout = poll_timeout
        self.max_chars = min(max_chars, _TELEGRAM_LIMIT)
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
        if self.allowed_users is not None and user_id not in self.allowed_users:
            return None
        text = str(message.get("text") or "").strip()
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if not text or not chat_id:
            return None
        return InboundMessage(text=text, chat_id=chat_id, platform=self.platform, user=user_id)

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
                    inbound = self._message_from_update(update)
                    if inbound is None:
                        continue
                    self._post(client, inbound.chat_id, route(inbound) or "(no reply)")

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
