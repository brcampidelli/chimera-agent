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

from chimera.api.attachments import MAX_BYTES
from chimera.server.gateway import InboundMessage, chunk_text, run_with_indicator
from chimera.server.inbound_media import (
    MEDIA_UNSTATED_LENGTH_REPLY,
    VOICE_RATE_REPLY,
    MediaTooLarge,
    read_bounded,
    refusal_reply,
    too_long_reply,
)
from chimera.server.voice import (
    VOICE_MAX_SECONDS,
    VOICE_UNSTATED_MAX_BYTES,
    VoiceRateLimiter,
    discard_reply_audio,
)
from chimera.telemetry import get_logger

_log = get_logger("server.telegram")
_TELEGRAM_LIMIT = 4096
_API = "https://api.telegram.org"


def _seconds(value: object) -> float:
    """A length the platform reported, as seconds; 0.0 when it is absent or not a number."""
    try:
        return float(value or 0.0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


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
        voice_max_seconds: int = VOICE_MAX_SECONDS,
        voice_per_user: int = 0,
    ) -> None:
        self.token = token
        self.allowed_users = allowed_users  # None = anyone; else an allowlist of user ids
        self.pairing_flow: Any = None
        self.respond_to_bots = respond_to_bots
        self.poll_timeout = poll_timeout
        self.max_chars = min(max_chars, _TELEGRAM_LIMIT)
        self.inbound_media = inbound_media
        self.voice_max_seconds = voice_max_seconds
        self.voice_limit = VoiceRateLimiter(voice_per_user)
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
        media_seconds = 0.0
        media_bytes = 0
        if message.get("voice") or message.get("audio"):
            media_kind = "audio"
            audio = message.get("voice") or message.get("audio") or {}
            media_file_id = str(audio.get("file_id", ""))
            media_name = str(audio.get("file_name") or "voice.ogg")
            # Telegram states the length of a voice note, so an over-long one is refused unfetched.
            media_seconds = _seconds(audio.get("duration"))
            media_bytes = int(audio.get("file_size") or 0)
        elif message.get("photo"):
            media_kind = "image"
            photo = message["photo"][-1]
            media_file_id = str(photo.get("file_id", ""))
            media_name = "photo.jpg"
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if not chat_id or (not text and not media_file_id):
            return None
        refused = bool(media_kind and not self.inbound_media)
        refusal_reply = ""
        if media_kind == "audio" and self.inbound_media:
            if media_seconds > self.voice_max_seconds:
                refused, refusal_reply = True, too_long_reply(self.voice_max_seconds)
            elif not media_seconds and media_bytes > VOICE_UNSTATED_MAX_BYTES:
                refused, refusal_reply = True, MEDIA_UNSTATED_LENGTH_REPLY
            elif not self.voice_limit.allow(user_id):
                refused, refusal_reply = True, VOICE_RATE_REPLY
        if refused:
            media_kind = ""
        return InboundMessage(
            text=text, chat_id=chat_id, platform=self.platform, user=user_id,
            from_bot=bool(sender.get("is_bot")), media_refusal=refused,
            media_refusal_reply=refusal_reply, media_kind=media_kind,
            media_file_id=media_file_id, media_name=media_name, media_seconds=media_seconds,
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
                        try:
                            self._post(client, inbound.chat_id, reply)
                        finally:
                            # Still tried when the text failed, and the audio is deleted either way.
                            self._answer_with_voice(client, inbound.chat_id, reply)
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
                # Telegram reports the size: refused before a byte is fetched (plan M6).
                if int(info.get("file_size") or 0) > MAX_BYTES:
                    raise MediaTooLarge(f"declared {info.get('file_size')} bytes")
                inbound.media_data = read_bounded(
                    client, f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}"
                )
        except (KeyError, ValueError, httpx.HTTPError) as exc:
            # The type, never the text: an httpx error's message carries the request URL, and a
            # file URL has the bot token in its path.
            _log.warning("telegram media download failed: %s", type(exc).__name__)
            inbound.media_refusal = True
            inbound.media_refusal_reply = refusal_reply(exc)
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

    def _answer_with_voice(self, client: Any, chat_id: str, reply: str) -> None:
        """Send the spoken reply, when the turn made one, as a voice note; then delete it.

        Telegram takes OGG/Opus as a voice note (``sendVoice``). Its failure is logged and never
        undoes the text, which was sent already.
        """
        audio = getattr(reply, "audio", None)
        if audio is None:
            return
        try:
            with audio.open("rb") as handle:
                response = client.post(
                    self._url("sendVoice"), data={"chat_id": chat_id},
                    files={"voice": (audio.name, handle, "audio/ogg")},
                )
            response.raise_for_status()
        except Exception as exc:  # the text is already with the chat
            _log.warning("telegram voice reply failed: %s", type(exc).__name__)
        finally:
            discard_reply_audio(audio)

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
