"""Slack adapter — make Chimera a native Slack bot.

Third platform on the same structure as Discord/Telegram (an
:class:`~chimera.server.gateway.Adapter` that receives + replies, plus a
:class:`~chimera.integrations.messaging.MessageSender` so the agent can send). Slack has no
long-poll, so receiving uses **Socket Mode** (``slack_sdk``, the optional ``messaging``
extra); sending uses the Web API ``chat.postMessage`` over plain ``httpx`` — no dependency.
Event parsing + filtering live in the pure :meth:`SlackAdapter._message_from_event`,
testable without a network. Tokens are read from the environment — never hard-coded.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from chimera.server.gateway import InboundMessage, chunk_text
from chimera.telemetry import get_logger

_log = get_logger("server.slack")
_SLACK_LIMIT = 3900
_POST_URL = "https://slack.com/api/chat.postMessage"


class SlackAdapter:
    """A platform transport (Socket Mode) + sender (Web API) for Slack."""

    name = "slack"
    platform = "slack"

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        *,
        allowed_users: set[str] | None = None,
        respond_to_bots: bool = False,
        max_chars: int = _SLACK_LIMIT,
    ) -> None:
        self.bot_token = bot_token  # xoxb-... (Web API, posting)
        self.app_token = app_token  # xapp-... (Socket Mode, receiving)
        self.allowed_users = allowed_users
        self.respond_to_bots = respond_to_bots
        self.max_chars = min(max_chars, _SLACK_LIMIT)
        self._client: Any = None
        self._stop = threading.Event()

    def _message_from_event(self, event: dict[str, Any]) -> InboundMessage | None:
        """Filter + build an InboundMessage from a Slack event (pure)."""
        if event.get("type") != "message":
            return None
        subtype = event.get("subtype")
        if subtype not in (None, "bot_message"):
            return None  # message_changed / deleted / channel_join / ...
        if (subtype == "bot_message" or event.get("bot_id")) and not self.respond_to_bots:
            return None  # a bot's message (incl. our own) — loop guard
        user_id = str(event.get("user", ""))
        if self.allowed_users is not None and user_id not in self.allowed_users:
            return None
        text = str(event.get("text") or "").strip()
        channel = str(event.get("channel", ""))
        if not text or not channel or not user_id:
            return None
        return InboundMessage(text=text, chat_id=channel, platform=self.platform, user=user_id)

    def start(self, route: Callable[[InboundMessage], str]) -> None:
        """Connect via Socket Mode and serve until :meth:`stop` (blocking)."""
        from slack_sdk.socket_mode import SocketModeClient  # optional `messaging` extra
        from slack_sdk.socket_mode.request import SocketModeRequest
        from slack_sdk.socket_mode.response import SocketModeResponse
        from slack_sdk.web import WebClient

        client = SocketModeClient(app_token=self.app_token, web_client=WebClient(token=self.bot_token))
        self._client = client

        def handle(cli: Any, req: SocketModeRequest) -> None:
            if req.type != "events_api":
                return
            cli.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
            event = req.payload.get("event", {})
            inbound = self._message_from_event(event)
            if inbound is None:
                return
            # Slack has no bot "typing" indicator, so mark the message with a ⏳ reaction while the turn
            # runs (received + working), then remove it — a best-effort stand-in for typing.
            ts = str(event.get("ts", ""))
            self._react(inbound.chat_id, ts, add=True)
            try:
                reply = route(inbound) or "(no reply)"
            finally:
                self._react(inbound.chat_id, ts, add=False)
            self.send(inbound.chat_id, reply)

        client.socket_mode_request_listeners.append(handle)
        _log.info("Slack adapter connecting (Socket Mode)")
        client.connect()
        self._stop.wait()  # block until stopped

    def _react(self, channel: str, ts: str, *, add: bool, name: str = "hourglass_flowing_sand") -> None:
        """Add/remove a working-indicator reaction on the user's message (best-effort; needs
        reactions:write). Slack has no bot typing indicator, so this stands in for one."""
        if not ts:
            return
        import httpx

        url = f"https://slack.com/api/reactions.{'add' if add else 'remove'}"
        try:
            with httpx.Client(timeout=10) as client:
                client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.bot_token}"},
                    json={"channel": channel, "timestamp": ts, "name": name},
                )
        except (httpx.HTTPError, ValueError) as exc:  # best-effort — never fail the turn
            _log.debug("slack reaction failed: %s", exc)

    def stop(self) -> None:
        self._stop.set()
        client = self._client
        if client is not None:
            close = getattr(client, "close", None) or getattr(client, "disconnect", None)
            if close is not None:
                close()

    def send(self, chat_id: str, text: str) -> str:
        """MessageSender: post to a channel via chat.postMessage (agent tool)."""
        import httpx

        headers = {"Authorization": f"Bearer {self.bot_token}"}
        sent = 0
        try:
            with httpx.Client(timeout=30) as client:
                for chunk in chunk_text(text, self.max_chars):
                    resp = client.post(_POST_URL, headers=headers, json={"channel": chat_id, "text": chunk})
                    body = resp.json()
                    if not body.get("ok", False):
                        return f"error: slack send failed: {body.get('error', 'unknown')}"
                    sent += 1
        except (httpx.HTTPError, ValueError) as exc:
            return f"error: slack send failed: {exc}"
        return f"sent {sent} message(s) to slack channel {chat_id}"
