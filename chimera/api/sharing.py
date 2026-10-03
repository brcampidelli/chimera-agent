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
import hashlib
import hmac
import itertools
import json
import secrets
import socket
import threading
import time
from collections import deque
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.telemetry import get_logger

_log = get_logger("api.sharing")

SHARES_FILE = "code_shares.json"

#: The key a link WITH an expiry keeps its token under on disk, instead of ``token``. A version of
#: this app from before expiry existed reads ``token`` and drops every other key, so it would load
#: an expired link as one that never expires and open its conversation again after a downgrade.
#: Under this key that version does not see the link at all: a downgrade loses the links that had
#: an expiry, which is the safe way round. Links with no expiry keep ``token``, readable by both.
EXPIRING_TOKEN = "expiring_token"

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
    #: When the token stops opening anything, as a Unix time; None is never — every link made
    #: before expiry existed, and every link made while ``CHIMERA_SHARE_EXPIRY_HOURS`` is empty.
    expires_at: float | None = None

    def expired(self, now: float | None = None) -> bool:
        return self.expires_at is not None and (time.time() if now is None else now) >= self.expires_at

    @property
    def id(self) -> str:
        """A name for the link that is not the link.

        The Security card lists every link and revokes one by this, so the token never has to
        travel back to the screen to be revoked — the per-conversation Share dialog shows the link
        itself, which is its job; a list of every way into this machine is not the place to print
        them all. A digest, not a slice: a prefix of the token would be part of the token.
        """
        return hashlib.sha256(self.token.encode("utf-8")).hexdigest()[:16]

    @property
    def hint(self) -> str:
        """The last four characters, the most the app ever shows of a secret (`config_api._hint`)."""
        return f"…{self.token[-4:]}" if len(self.token) > 8 else ""


#: Told which links a revoke removed, after the file is written. How a stream a guest already holds
#: open is ended at the revoke rather than at its next frame (`guest_api.build_guest_app`).
RevokeListener = Callable[[list[Share]], object]


class ShareStore:
    """The share tokens of one home, on disk."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._shares: list[Share] = []
        self._listeners: list[RevokeListener] = []
        self._load()

    def on_revoke(self, listener: RevokeListener) -> None:
        """Call ``listener`` with the removed links after every revoke, whichever route made it —
        the Share dialog, the access card, or deleting the conversation."""
        self._listeners.append(listener)

    def _revoked(self, gone: list[Share]) -> None:
        # Outside the store's lock: a listener reaches into the bus, which has its own.
        if not gone:
            return
        for listener in list(self._listeners):
            try:
                listener(gone)
            except Exception as exc:  # noqa: BLE001 -- the revoke is written; a listener cannot undo it
                _log.warning("a revoke listener failed: %s", exc)

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []
        except (OSError, ValueError) as exc:
            _log.warning("share store unreadable, starting empty: %s", exc)
            raw = []
        rows = raw if isinstance(raw, list) else []
        loaded = (_loaded(item) for item in rows if isinstance(item, dict))
        self._shares = [share for share in loaded if share is not None]

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.path,
            json.dumps([_stored(share) for share in self._shares], indent=2),
        )

    def mint(
        self, session_id: str, *, label: str = "", expires_in: float | None = None
    ) -> Share:
        """A new token for ``session_id``. Several may exist for one conversation — one per person
        the owner shared it with — so revoking one does not throw the others out.

        ``expires_in`` is seconds from now; None (or not positive) is a link that never expires."""
        now = time.time()
        share = Share(
            token=secrets.token_urlsafe(24), session_id=session_id, created_at=now,
            label=label.strip()[:80],
            expires_at=now + expires_in if expires_in is not None and expires_in > 0 else None,
        )
        with self._lock:
            self._shares.append(share)
            self._write()
        return share

    def _find(self, token: str) -> Share | None:
        """The share a token names, expired or not. Every stored token is compared, in constant
        time; the caller holds the lock."""
        found: Share | None = None
        for share in self._shares:
            if _same(share.token, token):
                found = share
        return found

    def resolve(self, token: str) -> Share | None:
        """The share a token OPENS, or None — and an expired link opens nothing.

        Checked here, at the one place a token turns into a conversation, rather than by a sweep
        that deletes old links: a sweep runs at some moment, and between its runs an expired link
        would still open. The expired link stays on disk so the owner sees on the Security card
        that it expired, instead of watching it vanish."""
        if not token:
            return None
        with self._lock:
            found = self._find(token)
        if found is None or found.expired():
            return None
        return found

    def for_session(self, session_id: str, *, include_expired: bool = False) -> list[Share]:
        """A conversation's links. Expired ones are left out unless asked for: a link that opens
        nothing is not a way in, so it must not hold a conversation out of the archive or be offered
        again as a link to copy."""
        with self._lock:
            return [
                s for s in self._shares
                if s.session_id == session_id and (include_expired or not s.expired())
            ]

    def all(self) -> list[Share]:
        """Every link this home holds, expired ones included, oldest first."""
        with self._lock:
            return list(self._shares)

    def _remove(self, doomed: Callable[[Share], bool]) -> list[Share]:
        with self._lock:
            gone = [s for s in self._shares if doomed(s)]
            if gone:
                self._shares = [s for s in self._shares if not doomed(s)]
                self._write()
        self._revoked(gone)
        return gone

    def revoke(self, token: str) -> bool:
        return bool(self._remove(lambda s: _same(s.token, token)))

    def revoke_id(self, share_id: str) -> Share | None:
        """Revoke the link whose :attr:`Share.id` this is; the share removed, or None."""
        gone = self._remove(lambda s: _same(s.id, share_id))
        return gone[0] if gone else None

    def revoke_session(self, session_id: str) -> int:
        """Every token of one conversation — what deleting the conversation must also do."""
        return len(self._remove(lambda s: s.session_id == session_id))

    def revoke_all(self) -> int:
        """Every link of every conversation."""
        return len(self._remove(lambda s: True))


def _same(stored: str, given: str) -> bool:
    """A constant-time compare of two strings, as bytes.

    ``hmac.compare_digest`` refuses a ``str`` holding a character outside ASCII with a TypeError,
    and the given side comes from a URL or a header: ``?t=%C3%A9`` on a guest route, or an id in the
    access card's DELETE path, was answered with a 500 instead of "this opens nothing". Encoded,
    every string compares; ``surrogatepass`` so a lone surrogate a decoder let through cannot raise
    either."""
    return hmac.compare_digest(
        stored.encode("utf-8", "surrogatepass"), given.encode("utf-8", "surrogatepass")
    )


def _stored(share: Share) -> dict[str, Any]:
    """One link as the file keeps it: under ``token`` when it never expires, under
    :data:`EXPIRING_TOKEN` when it does."""
    row: dict[str, Any] = {
        "session_id": share.session_id,
        "created_at": share.created_at,
        "label": share.label,
        "expires_at": share.expires_at,
    }
    row["token" if share.expires_at is None else EXPIRING_TOKEN] = share.token
    return row


def _loaded(item: dict[str, Any]) -> Share | None:
    """One stored link, or None for a row that names no token or no conversation."""
    expiring = item.get(EXPIRING_TOKEN)
    token = str(expiring or item.get("token") or "")
    session_id = str(item.get("session_id") or "")
    if not token or not session_id:
        return None
    expires_at = _expiry(item.get("expires_at"))
    if expiring and expires_at is None:
        # Filed as a link that expires, with no time anyone can read: expired, not "never".
        expires_at = 0.0
    return Share(
        token=token,
        session_id=session_id,
        created_at=float(item.get("created_at") or 0.0),
        label=str(item.get("label") or ""),
        expires_at=expires_at,
    )


def _expiry(value: Any) -> float | None:
    """A stored ``expires_at``, or None for a link that never expires (or a value nobody can read
    as a time — the file is the owner's own, and a hand-edit must not stop the app)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Subscriber:
    id: int
    name: str
    loop: asyncio.AbstractEventLoop
    queue: asyncio.Queue[dict[str, Any] | None]
    #: The :attr:`Share.id` of the link a guest came in with; empty for the owner's own window.
    #: What lets a revoke end exactly the streams that link opened, and nobody else's.
    share_id: str = ""
    #: The door a guest came through: ``"lan"`` for the network listener, empty for the owner's own
    #: ``/guest`` mount. Closing the network door ends the streams filed under it.
    door: str = ""


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

    def subscribe(
        self,
        session_id: str,
        *,
        name: str,
        loop: asyncio.AbstractEventLoop,
        share_id: str = "",
        door: str = "",
    ) -> Subscriber:
        sub = Subscriber(
            id=next(self._ids),
            name=name,
            loop=loop,
            queue=asyncio.Queue(),
            share_id=share_id,
            door=door,
        )
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

    def end_guests(
        self, share_ids: Collection[str] | None = None, *, door: str | None = None
    ) -> int:
        """End the open streams of guests: those that came in with one of ``share_ids`` (every link
        when None) and through ``door`` (every door when None). The owner's own windows are never
        ended here.

        A stream checks its link before every frame it sends, so a revoked link already delivers
        nothing more; this is what makes it stop NOW — the connection closes instead of idling on
        heartbeats until a frame comes along to be refused. The end is the same ``None`` a stream
        already reads as "stop", put on the subscriber's own loop."""
        with self._lock:
            targets = [
                sub
                for channel in self._channels.values()
                for sub in channel.subscribers.values()
                if sub.share_id
                and (share_ids is None or sub.share_id in share_ids)
                and (door is None or sub.door == door)
            ]
        for sub in targets:
            with contextlib.suppress(RuntimeError):  # a closed loop: that stream is already gone
                sub.loop.call_soon_threadsafe(sub.queue.put_nowait, None)
        return len(targets)

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
