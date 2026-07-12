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
from chimera.telemetry import get_logger

_log = get_logger("server.gateway")


def chunk_text(text: str, size: int) -> list[str]:
    """Split text into <=size chunks for a platform's message-length limit (empty -> [])."""
    return [text[start : start + size] for start in range(0, len(text), size)]


def run_with_indicator(
    route: Callable[[InboundMessage], str],
    inbound: InboundMessage,
    *,
    ping: Callable[[], None],
    interval: float = 4.0,
) -> str:
    """Run the (blocking) ``route(inbound)`` while re-pinging a fading "typing" indicator.

    Some platforms' typing signals expire (Telegram's chat action after ~5s, Signal's typing message),
    so they must be re-sent to stay visible for a whole turn. This runs the agent turn in a thread and
    calls ``ping`` immediately and every ``interval`` seconds until it returns. ``ping`` failures are
    swallowed — the indicator is best-effort and must never fail or delay the reply. Returns the reply.
    """
    import threading

    box: dict[str, str] = {}
    error: dict[str, BaseException] = {}

    def work() -> None:
        # Capture a route() crash instead of letting it die silently in the daemon thread and
        # surface as a benign "(no reply)" — for an honesty-first system a crash must not be
        # misrepresented as an empty answer.
        try:
            box["reply"] = route(inbound) or "(no reply)"
        except Exception as exc:  # noqa: BLE001 — carried out of the thread and re-raised after join
            error["exc"] = exc

    def _ping() -> None:
        try:
            ping()
        except Exception as exc:  # noqa: BLE001 — a typing ping must never break the turn
            _log.debug("typing indicator ping failed: %s", exc)

    worker = threading.Thread(target=work, daemon=True)
    _ping()
    worker.start()
    while True:
        worker.join(timeout=interval)
        if not worker.is_alive():
            break
        _ping()
    if "exc" in error:
        raise error["exc"]
    return box.get("reply", "(no reply)")


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
