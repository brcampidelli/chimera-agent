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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from chimera.core.code_session import _accepts
from chimera.interface import ChatSession, render
from chimera.telemetry import get_logger

_log = get_logger("server.gateway")


def with_warnings(answer: str, warnings: Sequence[str]) -> str:
    """``answer`` with one ``⚠`` line per warning under it, or ``answer`` alone when there are none."""
    if not warnings:
        return answer
    lines = "\n".join(f"⚠ {w}" for w in warnings)
    return f"{answer}\n\n{lines}" if answer.strip() else lines


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


#: How many turns a gateway session keeps in memory.
#:
#: This is the one surface where an unbounded transcript is a leak with no upside: a session per
#: chat, alive for as long as the process, and persisted nowhere — there is no file for the older
#: turns to be the record of. ``ChatSession`` used to apply this bound to everybody, including the
#: sessions ``SessionManager`` writes to disk, so from turn 51 every save rewrote the file without
#: turn 1. The bound was not wrong; its home was.
GATEWAY_MAX_TURNS = 50


class MessageGateway:
    """Routes each chat to its own ChatSession, created lazily via a factory."""

    def __init__(
        self,
        session_factory: Callable[[], ChatSession],
        *,
        max_turns: int | None = GATEWAY_MAX_TURNS,
        warnings_in_reply: bool = False,
    ) -> None:
        self._factory = session_factory
        self._sessions: dict[str, ChatSession] = {}
        self._max_turns = max_turns
        #: Append the turn's warnings, and why it was cut short, under the answer. On for a chat
        #: platform, where the reply is all the person sees. Off for the HTTP ``/chat`` route, whose
        #: ``reply`` field a program reads as the answer.
        self._warnings_in_reply = warnings_in_reply

    def session_for(self, key: str) -> ChatSession:
        if key not in self._sessions:
            session = self._factory()
            # Applied here rather than asked of every caller's factory: the factories are built in
            # `serve`, in tests and in three platform adapters, and a bound that has to be
            # remembered at five call sites is a bound that is missing at one of them.
            #
            # Through `getattr`, because what this class actually needs from a session is `send`:
            # the HTTP transport's own tests drive it with a two-method fake, and a gateway that
            # crashed on a session without this field would be enforcing a bound by refusing to
            # route. Only an unset bound is filled in — a session that asked for one keeps it.
            if getattr(session, "max_turns", self._max_turns) is None:
                session.max_turns = self._max_turns
            self._sessions[key] = session
        return self._sessions[key]

    def on_message(self, message: InboundMessage) -> str:
        """Route a message to its chat's session and return the reply."""
        session = self.session_for(message.key)
        verbose = getattr(session, "send_verbose", None)
        if not self._warnings_in_reply or verbose is None or not _accepts(verbose, "on_notice"):
            return session.send(message.text)
        # The bot used to call `send`, which takes no callbacks, so a warning sent while the turn
        # ran went nowhere and a reply cut off by a limit read exactly like a finished one. The
        # terminal prints both; on a chat platform the reply is the only place left to say them.
        said: list[str] = []

        def hear(code: str, text: str, _data: dict[str, Any]) -> None:
            line = render.notice_text(code, text)
            if line not in said:
                said.append(line)

        report = verbose(message.text, on_notice=hear)
        cut = render.cut_short_text(report)
        if cut:
            said.append(cut)
        return with_warnings(report.answer, said)

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
