"""Platform-agnostic outbound messaging.

An agent should be able to *act on* the platforms it talks on — reply on Discord, post to
Telegram, message Slack. A :class:`MessageSender` is the minimal per-platform send
capability; a :class:`SenderRegistry` collects them; :class:`SendMessageTool` exposes them
to the agent as a single tool. The tool and registry are pure and fully testable (a fake
sender); real adapters (Discord, ...) register a live sender when they start.
"""

from __future__ import annotations

from typing import Any, Protocol

from chimera.tools.base import Tool


class MessageSender(Protocol):
    """The minimal outbound capability for one platform."""

    platform: str

    def send(self, chat_id: str, text: str) -> str: ...


class SenderRegistry:
    """Collects per-platform senders; routes a send to the right one."""

    def __init__(self) -> None:
        self._senders: dict[str, MessageSender] = {}

    def register(self, sender: MessageSender) -> None:
        self._senders[sender.platform] = sender

    def platforms(self) -> list[str]:
        return sorted(self._senders)

    def send(self, platform: str, chat_id: str, text: str) -> str:
        sender = self._senders.get(platform)
        if sender is None:
            have = ", ".join(self.platforms()) or "none"
            return f"error: no sender for platform {platform!r} (connected: {have})"
        try:
            return sender.send(chat_id, text)
        except Exception as exc:  # noqa: BLE001 - a platform error must not crash the agent loop
            return f"error: send to {platform} failed: {exc}"


class SendMessageTool(Tool):
    """Lets the agent send a message to a chat on a connected platform."""

    name = "send_message"
    description = (
        "Send a text message to a chat/channel on a connected messaging platform "
        "(e.g. discord). Use the platform name, the destination chat/channel id, and the text."
    )

    def __init__(self, registry: SenderRegistry) -> None:
        self.registry = registry
        self.parameters = {
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "Target platform, e.g. 'discord'."},
                "chat_id": {"type": "string", "description": "Destination chat/channel id."},
                "text": {"type": "string", "description": "The message text to send."},
            },
            "required": ["platform", "chat_id", "text"],
        }

    def run(self, **kwargs: Any) -> str:
        platform = str(kwargs.get("platform", "")).strip()
        chat_id = str(kwargs.get("chat_id", "")).strip()
        text = str(kwargs.get("text", ""))
        if not (platform and chat_id and text):
            return "error: send_message requires 'platform', 'chat_id', and 'text'"
        return self.registry.send(platform, chat_id, text)
