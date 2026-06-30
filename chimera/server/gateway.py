"""Messaging gateway — route inbound platform messages into per-chat sessions.

The gateway is the hub messaging is built on: each chat (a Discord channel, a
Telegram thread, an HTTP client) gets its own :class:`~chimera.interface.ChatSession`,
so conversations keep separate context while sharing long-term memory. Platform
**adapters** translate their events into an :class:`InboundMessage` and send the
reply back; the routing core (:meth:`MessageGateway.on_message`) is pure and
testable. The :class:`LocalAdapter` (in-process) and the HTTP server are the first
two transports — Discord/Telegram adapters plug in the same way.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from chimera.interface import ChatSession


@dataclass
class InboundMessage:
    """A message arriving from some platform."""

    text: str
    chat_id: str = "default"
    platform: str = "local"
    user: str = "user"

    @property
    def key(self) -> str:
        """The session key — one session per (platform, chat)."""
        return f"{self.platform}:{self.chat_id}"


class MessageGateway:
    """Routes each chat to its own ChatSession, created lazily via a factory."""

    def __init__(self, session_factory: Callable[[], ChatSession]) -> None:
        self._factory = session_factory
        self._sessions: dict[str, ChatSession] = {}

    def session_for(self, key: str) -> ChatSession:
        if key not in self._sessions:
            self._sessions[key] = self._factory()
        return self._sessions[key]

    def on_message(self, message: InboundMessage) -> str:
        """Route a message to its chat's session and return the reply."""
        return self.session_for(message.key).send(message.text)

    @property
    def active_chats(self) -> int:
        return len(self._sessions)


class Adapter(Protocol):
    """A platform transport: feed inbound messages to ``on_message``, send replies."""

    name: str

    def start(self, on_message: Callable[[InboundMessage], str]) -> None: ...

    def stop(self) -> None: ...


class LocalAdapter:
    """In-process loopback transport — drive it directly (tests, a local REPL)."""

    name = "local"

    def __init__(self) -> None:
        self._on_message: Callable[[InboundMessage], str] | None = None

    def start(self, on_message: Callable[[InboundMessage], str]) -> None:
        self._on_message = on_message

    def stop(self) -> None:
        self._on_message = None

    def feed(self, text: str, *, chat_id: str = "default") -> str:
        if self._on_message is None:
            raise RuntimeError("adapter not started")
        return self._on_message(InboundMessage(text=text, chat_id=chat_id, platform=self.name))
