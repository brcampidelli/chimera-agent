"""WhatsApp Cloud API — sender + inbound parser.

Unlike Discord/Telegram/Slack, WhatsApp is **push-based**: messages arrive at a webhook you
host (a Meta app + a public URL + verification), not a connection Chimera opens. So this
ships the clean, testable halves — a :class:`~chimera.integrations.messaging.MessageSender`
(so the agent can notify over WhatsApp via ``send_message``) and a pure inbound parser for
when a webhook delivers a message. Full two-way needs the Meta webhook wired to a public
endpoint; the parser is the building block for that. Credentials come from the environment.
"""

from __future__ import annotations

from typing import Any

from chimera.server.gateway import InboundMessage
from chimera.telemetry import get_logger

_log = get_logger("server.whatsapp")
_WHATSAPP_LIMIT = 4096


class WhatsAppSender:
    """Send WhatsApp text messages via the Cloud API; parse inbound webhook payloads."""

    platform = "whatsapp"

    def __init__(self, access_token: str, phone_number_id: str, *, api_version: str = "v20.0") -> None:
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.api_version = api_version

    def _url(self) -> str:
        return f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"

    def send(self, chat_id: str, text: str) -> str:
        """MessageSender: send a text message to a recipient phone number (E.164)."""
        import httpx

        headers = {"Authorization": f"Bearer {self.access_token}"}
        body = {
            "messaging_product": "whatsapp",
            "to": chat_id,
            "type": "text",
            "text": {"body": text[:_WHATSAPP_LIMIT] or "(no reply)"},
        }
        try:
            with httpx.Client(timeout=30) as client:
                data = client.post(self._url(), headers=headers, json=body).json()
        except (httpx.HTTPError, ValueError) as exc:
            return f"error: whatsapp send failed: {exc}"
        if isinstance(data, dict) and data.get("error"):
            return f"error: whatsapp send failed: {data['error'].get('message', 'unknown')}"
        return f"sent message to whatsapp {chat_id}"

    @staticmethod
    def parse_inbound(payload: dict[str, Any]) -> InboundMessage | None:
        """Parse a WhatsApp webhook payload into an InboundMessage (pure). None if not a text message."""
        try:
            value = payload["entry"][0]["changes"][0]["value"]
            messages = value.get("messages")
            if not messages:
                return None  # a status/delivery update, not an inbound message
            message = messages[0]
            if message.get("type") != "text":
                return None
            text = str(message.get("text", {}).get("body", "")).strip()
            sender = str(message.get("from", ""))
        except (KeyError, IndexError, TypeError):
            return None
        if not text or not sender:
            return None
        return InboundMessage(text=text, chat_id=sender, platform="whatsapp", user=sender)
