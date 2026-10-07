"""WhatsApp Cloud API — sender + inbound parser.

Unlike Discord/Telegram/Slack, WhatsApp is **push-based**: messages arrive at a webhook you
host (a Meta app + a public URL + verification), not a connection Chimera opens. So this
ships the clean, testable halves — a :class:`~chimera.integrations.messaging.MessageSender`
(so the agent can notify over WhatsApp via ``send_message``) and a pure inbound parser for
when a webhook delivers a message. Full two-way needs the Meta webhook wired to a public
endpoint; the parser is the building block for that. Credentials come from the environment.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from chimera.providers.failover import policy_block
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
            kind = str(message.get("type", ""))
            media_kind = "audio" if kind == "audio" else "image" if kind == "image" else ""
            if kind != "text" and not media_kind:
                return None
            text = str(message.get("text", {}).get("body", "")).strip() if kind == "text" else ""
            sender = str(message.get("from", ""))
            media = message.get(kind, {}) if media_kind else {}
            media_id = str(media.get("id", "")) if isinstance(media, dict) else ""
            media_name = str(media.get("filename") or ("voice.ogg" if kind == "audio" else "photo.jpg"))
        except (KeyError, IndexError, TypeError):
            return None
        if not sender or (not text and not media_id):
            return None
        return InboundMessage(
            text=text, chat_id=sender, platform="whatsapp", user=sender,
            media_kind=media_kind, media_file_id=media_id, media_name=media_name,
        )


#: A Graph API media handle: digits only. Anything else in a payload is not one Meta issued.
_MEDIA_ID = re.compile(r"[0-9]{1,32}")
#: Where the Graph API hands out media downloads. Suffix-matched on the parsed hostname, never on
#: the raw URL: ``https://lookaside.fbsbx.com@evil.test/`` starts with the right text.
_MEDIA_HOSTS = ("fbsbx.com", "facebook.com", "whatsapp.net")
_MEDIA_MAX_BYTES = 20 * 1024 * 1024


def _is_meta_media_url(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    return any(host == root or host.endswith("." + root) for root in _MEDIA_HOSTS)


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
        allowed_numbers: set[str] | None = None,
        inbound_media: bool = False,
        media_downloader: Callable[[str], bytes] | None = None,
        pairing_flow: Any = None,
    ) -> None:
        self.sender = sender
        self.verify_token = verify_token
        self.route = route
        # Meta app secret for inbound HMAC verification. When set, an unsigned/mis-signed webhook POST
        # is rejected — otherwise anyone who knows the URL could forge a message and make the agent
        # send an outbound reply to an attacker-chosen number. Existing hooks remain supported without it.
        self.app_secret = app_secret
        # Who may talk to the agent here, as the other adapters' ``allowed_users``: None = anyone,
        # which is what this webhook did before it had the parameter. Compared as digits because
        # Meta sends ``from`` as bare digits ("5511987654321") and an owner writes their own number
        # the way a phone shows it ("+55 11 98765-4321"); a literal comparison would lock them out.
        self.pairing_flow = pairing_flow
        self.allowed_numbers = (
            None if allowed_numbers is None else {_digits(n) for n in allowed_numbers if _digits(n)}
        )
        self.inbound_media = inbound_media
        self.media_downloader = media_downloader

    def verify_signature(self, raw_body: bytes, signature: str | None) -> bool:
        """True if ``X-Hub-Signature-256`` is a valid HMAC-SHA256(app_secret, raw_body).

        Returns True (unverified) when no app_secret is configured; the CLI requires it for new installs.
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

    def _download_media(self, media_id: str) -> bytes:
        """Fetch an inbound media object through the Graph API, and nowhere else.

        ``media_id`` comes out of the webhook payload, and the follow-up URL out of a response —
        neither is ours, and both requests carry the owner's access token. So the id must be the
        all-digits handle Meta issues (a ``../`` or ``?`` would steer the Graph request) and the
        download host must be Meta's media CDN over https: anything else would hand the bearer
        token to whatever host the URL named, and fetch from inside the owner's network.
        """
        import httpx

        if not _MEDIA_ID.fullmatch(media_id):
            raise ValueError("media id is not a Graph API media id")
        headers = {"Authorization": f"Bearer {self.sender.access_token}"}
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            meta = client.get(f"https://graph.facebook.com/{self.sender.api_version}/{media_id}", headers=headers)
            meta.raise_for_status()
            url = str(meta.json().get("url", ""))
            if not _is_meta_media_url(url):
                raise ValueError("media download URL is not on Meta's media host")
            response = client.get(url, headers=headers)
            response.raise_for_status()
            if len(response.content) > _MEDIA_MAX_BYTES:
                raise ValueError("inbound media exceeds the size limit")
            return response.content

    def on_message(self, payload: dict[str, Any]) -> int:
        """Handle an inbound webhook POST: route the message and reply. Returns count handled."""
        message = WhatsAppSender.parse_inbound(payload)
        if message is None:
            return 0
        if self.pairing_flow is not None and _digits(message.user) not in self.pairing_flow.allowed_users:
            self.pairing_flow.authorize(_digits(message.user), message.text)
            return 0
        if self.allowed_numbers is not None and _digits(message.user) not in self.allowed_numbers:
            # No turn and no reply: a reply would both confirm the number reaches a bot and spend the
            # owner's money answering a stranger. The number is logged so the owner can add it.
            _log.debug("whatsapp: ignored a message from %s (not in the allowlist)", message.user)
            return 0
        if message.media_kind:
            if not self.inbound_media:
                message.media_kind = ""
                message.media_refusal = True
                reply = self.route(message)
                self.sender.send(message.chat_id, reply)
                return 1
            try:
                if not _MEDIA_ID.fullmatch(message.media_file_id):
                    raise ValueError("media id is not a Graph API media id")
                message.media_data = (
                    self.media_downloader(message.media_file_id)
                    if self.media_downloader is not None
                    else self._download_media(message.media_file_id)
                )
            except Exception as exc:
                _log.warning("whatsapp media download failed: %s", exc)
                message.media_kind = ""
                message.media_refusal = True
                reply = self.route(message)
                self.sender.send(message.chat_id, reply)
                return 1
        try:
            reply = self.route(message)
        except Exception as exc:
            # A content-policy refusal is answered in the chat, as the bots answer one (study 29
            # P5.7). It cannot be left to the gateway: this webhook shares the HTTP server's
            # `MessageGateway`, built for `/chat`, where a refusal must stay an error because a
            # program reads the reply as the answer. Here it escaped instead, Meta's POST failed,
            # and the person on WhatsApp got nothing at all. Anything else still raises as before.
            block = policy_block(exc)
            if block is None:
                raise
            _log.warning("whatsapp: content-policy refusal for %s: %s", message.chat_id, exc)
            reply = block.chat_sentence()
        if reply:
            self.sender.send(message.chat_id, reply)
        return 1


def _digits(number: str | None) -> str:
    """A phone number reduced to its digits — the form Meta's webhook uses for ``from``."""
    return "".join(ch for ch in str(number or "") if ch.isdigit())
