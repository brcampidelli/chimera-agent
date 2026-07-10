"""Signal adapter — via a signal-cli-rest-api bridge.

Signal has no official API, so Chimera talks to a **signal-cli-rest-api** instance (the
``bbernhard/signal-cli-rest-api`` Docker container you run and link to your number). That
bridge is plain HTTP, so this adapter is the same shape as Telegram — poll ``GET
/v1/receive/<number>`` and send ``POST /v2/send`` over ``httpx``, no Python dependency. The
envelope parsing + filtering live in the pure :meth:`SignalAdapter._message_from_envelope`,
tested without a network. The bridge URL + number come from the environment.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from functools import partial
from typing import Any

from chimera.server.gateway import InboundMessage, run_with_indicator
from chimera.telemetry import get_logger

_log = get_logger("server.signal")


class SignalAdapter:
    """A platform transport + sender for Signal via a signal-cli-rest-api bridge."""

    name = "signal"
    platform = "signal"

    def __init__(
        self,
        api_url: str,
        number: str,
        *,
        allowed_users: set[str] | None = None,
        poll_interval: int = 2,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.number = number  # this bot's own Signal number (E.164)
        self.allowed_users = allowed_users
        self.poll_interval = poll_interval
        self._running = False

    def _message_from_envelope(self, item: dict[str, Any]) -> InboundMessage | None:
        """Build an InboundMessage from one signal-cli-rest-api receive item (pure)."""
        envelope = item.get("envelope") if isinstance(item, dict) else None
        if not isinstance(envelope, dict):
            return None
        data = envelope.get("dataMessage")
        if not isinstance(data, dict):
            return None  # a receipt/typing/sync update, not a message
        text = str(data.get("message") or "").strip()
        source = str(envelope.get("source") or "")
        if not text or not source:
            return None
        if self.allowed_users is not None and source not in self.allowed_users:
            return None
        return InboundMessage(text=text, chat_id=source, platform=self.platform, user=source)

    def _send_via(self, client: Any, chat_id: str, text: str) -> None:
        client.post(
            f"{self.api_url}/v2/send",
            json={"message": text, "number": self.number, "recipients": [chat_id]},
        )

    def _typing(self, client: Any, chat_id: str, *, on: bool) -> None:
        """Start/stop the Signal typing indicator via the bridge (best-effort; expires, so refreshed)."""
        try:
            client.request(
                "PUT" if on else "DELETE",
                f"{self.api_url}/v1/typing-indicator/{self.number}",
                json={"recipient": chat_id},
            )
        except Exception as exc:  # noqa: BLE001 — typing is best-effort, never fail the turn
            _log.debug("signal typing indicator failed: %s", exc)

    def start(self, route: Callable[[InboundMessage], str]) -> None:
        """Poll the bridge for messages and reply, until :meth:`stop` (blocking)."""
        import httpx

        self._running = True
        with httpx.Client(timeout=self.poll_interval + 60) as client:
            while self._running:
                try:
                    items = client.get(f"{self.api_url}/v1/receive/{self.number}").json()
                except (httpx.HTTPError, ValueError) as exc:
                    _log.warning("signal poll failed: %s", exc)
                    time.sleep(3)
                    continue
                for item in items if isinstance(items, list) else []:
                    inbound = self._message_from_envelope(item)
                    if inbound is None:
                        continue
                    # Show a typing indicator while the (blocking) turn runs; it expires, so refresh.
                    reply = run_with_indicator(
                        route, inbound,
                        ping=partial(self._typing, client, inbound.chat_id, on=True),
                    )
                    self._typing(client, inbound.chat_id, on=False)
                    self._send_via(client, inbound.chat_id, reply)
                time.sleep(self.poll_interval)

    def stop(self) -> None:
        self._running = False

    def send(self, chat_id: str, text: str) -> str:
        """MessageSender: send a message to a Signal recipient (agent tool)."""
        import httpx

        try:
            with httpx.Client(timeout=30) as client:
                self._send_via(client, chat_id, text)
        except httpx.HTTPError as exc:
            return f"error: signal send failed: {exc}"
        return f"sent message to signal {chat_id}"
