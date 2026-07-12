"""WhatsApp Cloud API — sender + inbound parser.

Unlike Discord/Telegram/Slack, WhatsApp is **push-based**: messages arrive at a webhook you
host (a Meta app + a public URL + verification), not a connection Chimera opens. So this
ships the clean, testable halves — a :class:`~chimera.integrations.messaging.MessageSender`
(so the agent can notify over WhatsApp via ``send_message``) and a pure inbound parser for
when a webhook delivers a message. Full two-way needs the Meta webhook wired to a public
endpoint; the parser is the building block for that. Credentials come from the environment.
"""

from __future__ import annotations

from collections.abc import Callable
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


class WhatsAppWebhook:
    """Two-way WhatsApp over an inbound webhook: Meta verification + message routing.

    Wire the Meta app's webhook at ``https://<your-host>/whatsapp``. The verification and
    routing are pure/testable; only the HTTP transport (public URL) lives outside.
    """

    def __init__(
        self,
        sender: WhatsAppSender,
        verify_token: str,
        route: Callable[[InboundMessage], str],
        *,
        app_secret: str | None = None,
    ) -> None:
        self.sender = sender
        self.verify_token = verify_token
        self.route = route
        # Meta app secret for inbound HMAC verification. When set, an unsigned/mis-signed webhook POST
        # is rejected — otherwise anyone who knows the URL could forge a message and make the agent
        # send an outbound reply to an attacker-chosen number. Opt-in (None = unverified, as before).
        self.app_secret = app_secret

    def verify_signature(self, raw_body: bytes, signature: str | None) -> bool:
        """True if ``X-Hub-Signature-256`` is a valid HMAC-SHA256(app_secret, raw_body).

        Returns True (unverified) when no app_secret is configured — verification is opt-in.
        """
        if not self.app_secret:
            return True
        import hashlib
        import hmac

        if not signature or not signature.startswith("sha256="):
            return False
        expected = hmac.new(self.app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature[len("sha256=") :])

    def verify(self, params: dict[str, str]) -> str | None:
        """Meta webhook verification (GET): return hub.challenge when the token matches."""
        if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == self.verify_token:
            return params.get("hub.challenge")
        return None

    def on_message(self, payload: dict[str, Any]) -> int:
        """Handle an inbound webhook POST: route the message and reply. Returns count handled."""
        message = WhatsAppSender.parse_inbound(payload)
        if message is None:
            return 0
        reply = self.route(message)
        if reply:
            self.sender.send(message.chat_id, reply)
        return 1
