"""A coding conversation shared with a second person — by a token that reaches only that conversation.

Item 3 of the list audited on 2026-09-16 ("multiplayer"). The decision that shapes it, made by the
owner on 2026-09-17: **a token per shared session, never the server token.** The server token is
the owner's and opens everything; a share token opens one conversation — reading it, watching it
live, and sending a message into it — and nothing else. Revoking it closes that door and no other.

Two pieces live here, and nothing HTTP:

* :class:`ShareStore` — the tokens, in ``<home>/code_shares.json``. Minted with
  :func:`secrets.token_urlsafe`, resolved with a constant-time compare against every stored token
  (there are a handful; a timing side channel on a lookup is a cheap thing to close), and gone the
  moment they are revoked. Stored in clear, in the owner's home beside the conversations they
  open, so the owner can show the link again; the file is the same trust boundary as
  ``code_sessions/`` itself.
* :class:`SessionBus` — the live fan-out. A turn used to stream its frames to the one client that
  started it and to nobody else, which was right when the one client was the only person. Now
  every frame a turn emits is also published on the session's bus, numbered by the session, kept
  in a bounded ring for replay, and pushed to every subscriber — the owner's own screen included,
  which is how the owner sees a turn a guest started. Who is subscribed is the presence list.

What a share token does NOT do, stated because the word "guest" suggests less than it is: a guest
can ask the agent to do anything the owner can ask it, in the owner's project, with the owner's
tools — short of answering the governance cards, which stay on the owner's screen. The Share
dialog says so before the link is made.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import itertools
import json
import secrets
import socket
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.telemetry import get_logger

_log = get_logger("api.sharing")

SHARES_FILE = "code_shares.json"

#: Frames a session keeps for replay. A turn is a few hundred frames; twenty turns of history is
#: what a viewer who reconnects after a nap needs, and a viewer who has been away longer gets the
#: stored conversation instead.
RING = 4000

#: Openings a session remembers after they leave the ring. One per turn; a turn longer than the ring
#: needs its own back to be followed, and a conversation rarely runs more than a few at once.
OPENINGS = 64

#: A turn's frames from the run log, those with a session number in ``(after, before)``: what the
#: ring dropped of a turn it still holds part of. Each frame is the run log's record — ``event``,
#: ``session_seq`` and the payload's own fields.
Backfill = Callable[[str, int, int], list[dict[str, Any]]]


@dataclass(frozen=True)
class Share:
    """One token and the one conversation it opens."""

    token: str
    session_id: str
    created_at: float
    label: str = ""


class ShareStore:
    """The share tokens of one home, on disk."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._shares: list[Share] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, ValueError) as exc:
            _log.warning("share store unreadable, starting empty: %s", exc)
            raw = []
        self._shares = [
            Share(
                token=str(item.get("token") or ""),
                session_id=str(item.get("session_id") or ""),
                created_at=float(item.get("created_at") or 0.0),
                label=str(item.get("label") or ""),
            )
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict) and item.get("token") and item.get("session_id")
        ]

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps([share.__dict__ for share in self._shares], indent=2),
        )

    def mint(self, session_id: str, *, label: str = "") -> Share:
        """A new token for ``session_id``. Several may exist for one conversation — one per person
        the owner shared it with — so revoking one does not throw the others out."""
        share = Share(
            token=secrets.token_urlsafe(24), session_id=session_id, created_at=time.time(),
            label=label.strip()[:80],
        )
        with self._lock:
            self._shares.append(share)
            self._write()
        return share

    def resolve(self, token: str) -> Share | None:
        """The share a token opens, or None. Every stored token is compared, in constant time."""
        if not token:
            return None
        found: Share | None = None
        with self._lock:
            for share in self._shares:
                if hmac.compare_digest(share.token, token):
                    found = share
        return found

    def for_session(self, session_id: str) -> list[Share]:
        with self._lock:
            return [s for s in self._shares if s.session_id == session_id]

    def revoke(self, token: str) -> bool:
        with self._lock:
            before = len(self._shares)
            self._shares = [s for s in self._shares if not hmac.compare_digest(s.token, token)]
            if len(self._shares) != before:
                self._write()
                return True
        return False

    def revoke_session(self, session_id: str) -> int:
        """Every token of one conversation — what deleting the conversation must also do."""
        with self._lock:
            before = len(self._shares)
            self._shares = [s for s in self._shares if s.session_id != session_id]
            gone = before - len(self._shares)
            if gone:
                self._write()
        return gone


@dataclass
class Subscriber:
    id: int
    name: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any] | None]


@dataclass
class _Channel:
    seq: int = 0
    ring: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=RING))
    subscribers: dict[int, Subscriber] = field(default_factory=dict)
    #: The highest session number that has left the ring: a viewer asking from before it is asking
    #: for something the ring no longer has.
    dropped_through: int = 0
    #: Each turn's opening frame, kept past the ring — the frame a screen opens the turn's row on.
    openings: dict[str, dict[str, Any]] = field(default_factory=dict)
    last_publish: float = field(default_factory=time.monotonic)


class SessionBus:
    """Every frame of every turn of a session, numbered by the session, to everyone watching it.

    Thread-safe on the publishing side: a turn's frames come from the worker thread that runs it,
    and a subscriber's queue belongs to the event loop that serves its stream — so delivery goes
    through ``call_soon_threadsafe``, the same bridge the turn's own stream uses.
    """

    def __init__(self, *, ring: int | None = None, backfill: Backfill | None = None) -> None:
        self._lock = threading.Lock()
        self._channels: dict[str, _Channel] = {}
        self._ids = itertools.count(1)
        self._ring = RING if ring is None else ring
        self._backfill = backfill

    def _channel(self, session_id: str) -> _Channel:
        channel = self._channels.get(session_id)
        if channel is None:
            channel = _Channel(ring=deque(maxlen=self._ring))
            self._channels[session_id] = channel
        return channel

    def publish(
        self,
        session_id: str,
        event: str,
        payload: dict[str, Any],
        *,
        turn_id: str = "",
        author: str = "",
        keep: bool = True,
    ) -> dict[str, Any]:
        """Number and deliver one frame. ``keep=False`` delivers without remembering — a browser
        picture is tens of kilobytes and replay is for words, not for a page that has moved on."""
        with self._lock:
            channel = self._channel(session_id)
            channel.seq += 1
            frame = {
                "session_seq": channel.seq,
                "event": event,
                "turn_id": turn_id,
                "author": author,
                "payload": payload,
            }
            if keep:
                if channel.ring.maxlen is not None and len(channel.ring) == channel.ring.maxlen:
                    channel.dropped_through = int(channel.ring[0]["session_seq"])
                channel.ring.append(frame)
            if event == "turn_started" and turn_id:
                channel.openings[turn_id] = frame
                while len(channel.openings) > OPENINGS:
                    channel.openings.pop(next(iter(channel.openings)))
            channel.last_publish = time.monotonic()
            targets = list(channel.subscribers.values())
        for sub in targets:
            # A closed loop is a subscriber whose stream is gone; its own handler unsubscribes it.
            with contextlib.suppress(RuntimeError):
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, frame)
        return frame

    def replay(self, session_id: str, since: int = 0) -> list[dict[str, Any]]:
        """Every kept frame after ``since``, oldest first — and, when the ring has dropped some of
        what was asked for, what it dropped of the turns it still holds.

        A coding turn streams a frame per token, so a long one outgrows the ring. A screen that came
        back to it asked from the turn's opening and got the tail: no opening frame to draw the row
        on, and the answer without its start. The opening is kept past the ring and the rest comes
        from the run log, which keeps every frame of the turn with the session number it had. A
        turn wholly gone from the ring is not brought back — it has finished, and the stored
        conversation holds it; its opening alone would open a row nothing ever closes.
        """
        with self._lock:
            channel = self._channels.get(session_id)
            if channel is None:
                return []
            kept = [f for f in channel.ring if int(f["session_seq"]) > since]
            if since >= channel.dropped_through or not kept:
                return kept
            start = int(channel.ring[0]["session_seq"])
            turns: dict[str, dict[str, Any]] = {}
            for frame in kept:
                if frame["turn_id"] and frame["turn_id"] not in turns:
                    turns[frame["turn_id"]] = frame
            openings = {t: channel.openings.get(t) for t in turns}
        restored: list[dict[str, Any]] = []
        for turn_id, first in turns.items():
            opening = openings[turn_id]
            if opening is None or int(opening["session_seq"]) >= start:
                continue  # the ring holds this turn from its opening, or it has none (a background work)
            if int(opening["session_seq"]) > since:
                restored.append(opening)
            restored.extend(self._from_run_log(turn_id, str(first["author"]), since, start))
        if not restored:
            return kept
        restored.sort(key=lambda f: int(f["session_seq"]))
        return restored + kept

    def _from_run_log(self, turn_id: str, author: str, after: int, before: int) -> list[dict[str, Any]]:
        if self._backfill is None:
            return []
        try:
            records = self._backfill(turn_id, after, before)
        except Exception as exc:  # noqa: BLE001 -- a replay short of its start beats no replay
            _log.warning("could not bring back the start of turn %s: %s", turn_id, exc)
            return []
        out: list[dict[str, Any]] = []
        for record in records:
            number = record.get("session_seq")
            if not isinstance(number, int) or not after < number < before:
                continue
            payload = {k: v for k, v in record.items() if k not in ("event", "session_seq")}
            out.append({
                "session_seq": number, "event": str(record.get("event") or ""),
                "turn_id": turn_id, "author": author, "payload": payload,
            })
        return out

    def trim_idle(self, *, max_age: float, keep: set[str]) -> int:
        """Forget the kept frames of conversations nobody watches and nothing ran in for
        ``max_age`` seconds, except those in ``keep`` (the ones with a turn running). A screen that
        comes back to one reads the stored conversation, which holds every finished turn. The
        numbering is kept: a screen holding a number would otherwise wait for the count to climb
        back past it and miss every frame on the way."""
        now = time.monotonic()
        trimmed = 0
        with self._lock:
            for session_id, channel in self._channels.items():
                if session_id in keep or channel.subscribers or now - channel.last_publish <= max_age:
                    continue
                if channel.ring or channel.openings:
                    channel.ring.clear()
                    channel.openings.clear()
                    channel.dropped_through = channel.seq
                    trimmed += 1
        return trimmed

    def drop(self, session_id: str) -> None:
        """Forget a deleted conversation entirely."""
        with self._lock:
            self._channels.pop(session_id, None)

    def seq(self, session_id: str) -> int:
        with self._lock:
            channel = self._channels.get(session_id)
            return channel.seq if channel else 0

    def subscribe(self, session_id: str, *, name: str, loop: asyncio.AbstractEventLoop) -> Subscriber:
        sub = Subscriber(id=next(self._ids), name=name, loop=loop, queue=asyncio.Queue())
        with self._lock:
            self._channel(session_id).subscribers[sub.id] = sub
        self._announce_presence(session_id)
        return sub

    def unsubscribe(self, session_id: str, sub_id: int) -> None:
        with self._lock:
            channel = self._channels.get(session_id)
            if channel is None or sub_id not in channel.subscribers:
                return
            del channel.subscribers[sub_id]
        self._announce_presence(session_id)

    def presence(self, session_id: str) -> list[str]:
        """Who is watching, by the name each gave. Duplicates are two windows of one person."""
        with self._lock:
            channel = self._channels.get(session_id)
            if channel is None:
                return []
            return [s.name for s in channel.subscribers.values()]

    def _announce_presence(self, session_id: str) -> None:
        # Not kept in the ring: presence is a fact about now, and a replayed "joined" from an hour
        # ago would name someone who has left.
        self.publish(session_id, "presence", {"names": self.presence(session_id)}, keep=False)


def lan_addresses() -> list[str]:
    """The IPv4 addresses a machine on the same network could reach this one at, best first.

    Two sources, neither with a packet sent: the interface a route to a public address would use
    (a UDP socket is "connected" without any traffic, and its local end names that interface),
    then every address the host name resolves to. Loopback is never a LAN address.
    """
    found: list[str] = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, never reached
            found.append(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(str(info[4][0]))
    except (socket.gaierror, OSError):
        pass
    out: list[str] = []
    for address in found:
        if address.startswith("127.") or address == "0.0.0.0" or address in out:
            continue
        out.append(address)
    return out
