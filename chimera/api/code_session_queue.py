"""Durable, idempotent queue for coding turns.

A queue record is an inbox item, not a transcript turn. Its request carries the originating
surface's seams so a later executor cannot accidentally borrow the posture of the turn that
happened to finish first. State transitions are appended before they are acted on: in particular,
``running`` is durable before the agent starts, and a process that finds it after a restart marks it
interrupted rather than guessing whether repeating the work is safe.

Two fields that look alike are kept apart on purpose. ``source`` is a LABEL — which screen sent the
message (app, terminal, bridge, guest) — and it is shown, never obeyed. ``author`` is the identity
the turn runs as: empty for the owner, a guest's name only when the server's share-link route put it
there. A first version derived the second from the first (``author = source unless source == "app"``),
so a message the owner sent from the terminal or the MCP bridge ran as a GUEST — fewer tools, a guest
label on the receipt — and, read the other way, a body could have chosen its own identity.
"""

from __future__ import annotations

import builtins
import contextlib
import json
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from chimera.core.filelock import atomic_write_text

_SCREEN_QUEUE_IDS: set[str] = set()
_SCREEN_QUEUE_LOCK = threading.RLock()
# The same kind of marker for an item the desktop bridge forwarded (`VIA_BRIDGE_SCOPE_KEY`). Process
# memory only, so a file that claims it is ignored, and a restart drops it, as for the screen's.
_BRIDGE_QUEUE_IDS: set[str] = set()
_BRIDGE_QUEUE_LOCK = threading.RLock()

QueueState = Literal["pending", "running", "done", "failed", "interrupted", "cancelled"]

QUEUE_SOURCES = frozenset({"app", "terminal", "bridge", "guest"})
_STATES = frozenset({"pending", "running", "done", "failed", "interrupted", "cancelled"})
_TERMINAL = frozenset({"done", "failed", "interrupted", "cancelled"})

MAX_PENDING_PER_SESSION = 20
"""Waiting items one conversation holds. Each is a stored message and, later, a whole turn; a share
link or a looping client could otherwise pile up an unbounded file and an unbounded backlog of work."""

KEEP_TERMINAL = 200
"""Finished items kept when a quiet queue is compacted. They are what makes a late retry of a
``client_id`` observe completion instead of running twice; past this many, the oldest go."""


class QueueFullError(Exception):
    """The conversation already holds :data:`MAX_PENDING_PER_SESSION` waiting items."""


def _order(record: dict[str, Any]) -> tuple[float, str]:
    return float(record.get("created_at", 0)), str(record.get("id", ""))


@dataclass(frozen=True)
class QueueItem:
    id: str
    session_id: str
    client_id: str
    source: str
    request: dict[str, Any]
    state: QueueState
    created_at: float
    via_screen: bool = False
    via_bridge: bool = False
    position: int | None = None
    author: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "client_id": self.client_id,
            "source": self.source,
            "request": self.request,
            "state": self.state,
            "created_at": self.created_at,
            "position": self.position,
        }


def _screen_item(item_id: str) -> bool:
    with _SCREEN_QUEUE_LOCK:
        return item_id in _SCREEN_QUEUE_IDS


def _bridge_item(item_id: str) -> bool:
    with _BRIDGE_QUEUE_LOCK:
        return item_id in _BRIDGE_QUEUE_IDS


def _forget_marks(item_ids: list[str]) -> None:
    """Drop the screen and bridge marks of items that will not run again (finished or removed)."""
    with _SCREEN_QUEUE_LOCK:
        _SCREEN_QUEUE_IDS.difference_update(item_ids)
    with _BRIDGE_QUEUE_LOCK:
        _BRIDGE_QUEUE_IDS.difference_update(item_ids)


class CodeTurnQueue:
    """Append-only snapshots for each session's queue, stored beside its conversation."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)
        self.recovered: list[QueueItem] = []
        """Items found ``running`` at startup and marked interrupted; the owner is told of each."""
        # A running turn belongs to the process that marked it running. Replaying it after a crash
        # could repeat shell writes or other governed actions, so recovery is an explicit interruption.
        with self._lock:
            for path in self.root.glob("*.queue.jsonl"):
                session_id = path.name.removesuffix(".queue.jsonl")
                try:
                    records = self._latest(session_id)
                except ValueError:
                    continue
                for item in records.values():
                    if item.get("state") == "running":
                        item["state"] = "interrupted"
                        self._append(path, item)
                        self.recovered.append(self._item(item, records))
                self._compact_if_quiet(session_id, records)

    def _path(self, session_id: str) -> Path:
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_")[:64]
        if not safe or safe != session_id:
            raise ValueError("invalid session id")
        return self.root / f"{safe}.queue.jsonl"

    def _lines(self, session_id: str) -> list[str]:
        try:
            return self._path(session_id).read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []

    def _latest(self, session_id: str) -> dict[str, dict[str, Any]]:
        latest: dict[str, dict[str, Any]] = {}
        for line in self._lines(session_id):
            try:
                record = json.loads(line)
                if isinstance(record, dict) and isinstance(record.get("id"), str):
                    latest[record["id"]] = record
            except (ValueError, TypeError):
                # A torn final append is not an item and cannot make earlier snapshots disappear.
                continue
        return latest

    @staticmethod
    def _append(path: Path, record: dict[str, Any]) -> None:
        """Append one snapshot on a line of its own.

        A crash mid-append leaves a torn tail with no newline. Appending straight after it glued
        the next snapshot onto the torn bytes, the whole line failed to parse, and that snapshot —
        typically the ``running`` claim's ``done`` — was lost: the item stayed ``running`` and
        :meth:`claim_next` returned None for that conversation forever. So a missing newline is
        written first, which turns the torn bytes into one unparsable line and nothing more.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        lead = ""
        try:
            with path.open("rb") as existing:
                existing.seek(0, os.SEEK_END)
                if existing.tell() > 0:
                    existing.seek(-1, os.SEEK_END)
                    if existing.read(1) != b"\n":
                        lead = "\n"
        except FileNotFoundError:
            pass
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(lead + json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _compact_if_quiet(self, session_id: str, records: dict[str, dict[str, Any]]) -> None:
        """Rewrite the file as one snapshot per item once nothing is waiting or running.

        Only then: a rewrite that raced an executor's append would drop that append. With every
        item terminal there is no executor, and the rewrite is atomic, so a crash leaves either the
        old file or the new one. The newest :data:`KEEP_TERMINAL` items survive it.
        """
        if not records or any(r.get("state") not in _TERMINAL for r in records.values()):
            return
        lines = self._lines(session_id)
        kept = sorted(records.values(), key=_order)[-KEEP_TERMINAL:]
        if len(lines) <= len(kept):
            return
        text = "".join(
            json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in kept
        )
        atomic_write_text(self._path(session_id), text)
        # Items the rewrite dropped are gone from the queue: their process marks go with them.
        _forget_marks([str(r.get("id", "")) for r in records.values() if r not in kept])

    def enqueue(
        self,
        session_id: str,
        client_id: str,
        request: dict[str, Any],
        *,
        source: str = "app",
        author: str = "",
        via_screen: bool = False,
        via_bridge: bool = False,
        max_pending: int = MAX_PENDING_PER_SESSION,
    ) -> tuple[QueueItem, bool]:
        """Persist once per client id and return the item's current one-based queue position.

        ``author`` is set by the server, never from a request body: only the share-link route
        names a guest, and a guest's item is the only one labelled ``guest``. ``via_screen`` and
        ``via_bridge`` are the process's own marks (scope keys), kept in memory and never persisted.
        """
        if not isinstance(client_id, str) or not client_id or len(client_id) > 120:
            raise ValueError("client_id must contain 1 to 120 characters")
        if source not in QUEUE_SOURCES:
            raise ValueError("invalid queue source")
        if (source == "guest") != bool(author):
            raise ValueError("only a guest's item carries an author, and it is labelled guest")
        if via_bridge and source != "bridge":
            raise ValueError("a bridged item is labelled bridge")
        with self._lock:
            path = self._path(session_id)
            records = self._latest(session_id)
            for existing in records.values():
                if existing.get("client_id") == client_id:
                    existing_id = str(existing.get("id", ""))
                    if (
                        existing.get("request") != request
                        or existing.get("source") != source
                        or str(existing.get("author", "")) != author
                        # A mark held in memory and not asked for now is a different request.
                        or (_screen_item(existing_id) and not via_screen)
                        or (_bridge_item(existing_id) and not via_bridge)
                    ):
                        raise ValueError("client_id is already bound to a different queued request")
                    # A mark asked for now but not held is the restart case: the marks are process
                    # memory, so the stored item is returned UNMARKED (its mode does not apply). A
                    # retry never gains authority the item was not queued with.
                    # Return terminal state too: a delayed retry must observe completion, not
                    # create a second turn after the first one already made its side effects.
                    return self._item(existing, records), False
            waiting = sum(1 for r in records.values() if r.get("state") == "pending")
            if waiting >= max_pending:
                raise QueueFullError(
                    f"this conversation already has {max_pending} messages waiting; "
                    "send this one when one of them has run"
                )
            record_id = uuid.uuid4().hex
            record: dict[str, Any] = {
                "id": record_id,
                "session_id": session_id,
                "client_id": client_id,
                "source": source,
                "author": author,
                "request": request,
                "state": "pending",
                "created_at": time.time(),
            }
            self._append(path, record)
            if via_screen:
                with _SCREEN_QUEUE_LOCK:
                    _SCREEN_QUEUE_IDS.add(record_id)
            if via_bridge:
                with _BRIDGE_QUEUE_LOCK:
                    _BRIDGE_QUEUE_IDS.add(record_id)
            records[record["id"]] = record
            return self._item(record, records), True

    def claim_next(self, session_id: str) -> QueueItem | None:
        """Mark the oldest pending item running before returning it to an executor."""
        with self._lock:
            path = self._path(session_id)
            records = self._latest(session_id)
            if any(record.get("state") == "running" for record in records.values()):
                return None
            # Client ids remain reserved after completion or cancellation: a delayed retry cannot
            # turn a completed side effect into a second execution.
            pending = sorted(
                (r for r in records.values() if r.get("state") == "pending"), key=_order
            )
            if not pending:
                return None
            item = pending[0]
            item["state"] = "running"
            self._append(path, item)
            records[item["id"]] = item
            return self._item(item, records)

    def finish(self, session_id: str, item_id: str) -> bool:
        return self._transition(session_id, item_id, "done", from_state="running")

    def fail(self, session_id: str, item_id: str) -> bool:
        return self._transition(session_id, item_id, "failed", from_state="running")

    def requeue(self, session_id: str, item_id: str) -> bool:
        """Give a claimed item back, unstarted: the conversation refused it (full), not the turn."""
        return self._transition(session_id, item_id, "pending", from_state="running")

    def get(self, session_id: str, item_id: str) -> QueueItem | None:
        with self._lock:
            records = self._latest(session_id)
            record = records.get(item_id)
            return None if record is None else self._item(record, records)

    def cancel(self, session_id: str, item_id: str) -> bool:
        return self._transition(session_id, item_id, "cancelled", from_state="pending")

    def _transition(
        self, session_id: str, item_id: str, state: QueueState, *, from_state: str
    ) -> bool:
        with self._lock:
            records = self._latest(session_id)
            item = records.get(item_id)
            if item is None or item.get("state") != from_state:
                return False
            item["state"] = state
            self._append(self._path(session_id), item)
            if state in _TERMINAL:
                # An item that has finished (or left the queue) runs no more, so its process marks
                # go too. The client id stays reserved in the file; only the authority does not.
                _forget_marks([item_id])
            self._compact_if_quiet(session_id, records)
            return True

    def list(self, session_id: str) -> list[QueueItem]:
        with self._lock:
            records = self._latest(session_id)
            pending = sorted(
                (r for r in records.values() if r.get("state") == "pending"), key=_order
            )
            positions = {str(item["id"]): index for index, item in enumerate(pending, 1)}
            ordered = sorted(records.values(), key=_order)
            return [self._item(r, records, positions.get(str(r["id"]))) for r in ordered]

    def has_pending(self, session_id: str) -> bool:
        return any(item.state == "pending" for item in self.list(session_id))

    def sessions_with_pending(self) -> builtins.list[str]:
        """The conversations whose queue has something waiting: what the startup drain visits.

        ``builtins.list`` because this class's own :meth:`list` shadows the name here."""
        found: builtins.list[str] = []
        for path in sorted(self.root.glob("*.queue.jsonl")):
            session_id = path.name.removesuffix(".queue.jsonl")
            try:
                if self.has_pending(session_id):
                    found.append(session_id)
            except ValueError:
                continue
        return found

    def forget(self, session_id: str) -> None:
        """Remove the inbox with its conversation; deleted conversations must not be resurrected.

        An id no queue file could have (the store's own check is looser) has nothing to remove.
        """
        with self._lock, contextlib.suppress(FileNotFoundError, ValueError):
            records = self._latest(session_id)
            with _SCREEN_QUEUE_LOCK:
                _SCREEN_QUEUE_IDS.difference_update(records)
            with _BRIDGE_QUEUE_LOCK:
                _BRIDGE_QUEUE_IDS.difference_update(records)
            self._path(session_id).unlink()

    @staticmethod
    def _item(
        record: dict[str, Any], records: dict[str, dict[str, Any]], position: int | None = None
    ) -> QueueItem:
        if position is None and record.get("state") == "pending":
            pending = sorted(
                (r for r in records.values() if r.get("state") == "pending"), key=_order
            )
            position = next(
                (i for i, item in enumerate(pending, 1) if item["id"] == record["id"]), None
            )
        raw = record.get("state")
        state: QueueState = cast(QueueState, raw) if raw in _STATES else "interrupted"
        return QueueItem(
            id=str(record["id"]),
            session_id=str(record["session_id"]),
            client_id=str(record["client_id"]),
            source=str(record["source"]),
            request=dict(record.get("request", {})),
            state=state,
            created_at=float(record.get("created_at", 0)),
            position=position,
            author=str(record.get("author", "")),
            # This remains process-local; persisted provenance never authorizes bypass.
            via_screen=_screen_item(str(record.get("id", ""))),
            via_bridge=_bridge_item(str(record.get("id", ""))),
        )


class QueueDrainer:
    """Run ``drain(session_id)`` for a session, never twice at once, never missing a wake-up.

    A non-blocking lock alone loses wake-ups: a turn that ends while another drain holds the lock
    gives up, and if the holder had already looked at the queue and found nothing, the item the
    ending turn was waiting to release sits pending with nobody left to start it. So a request
    first raises a per-session flag, and the holder, after releasing, looks at the flag again and
    drains once more if anyone asked while it was busy.
    """

    def __init__(self, drain: Callable[[str], None]) -> None:
        self._drain = drain
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._wanted: set[str] = set()

    def request(self, session_id: str) -> None:
        with self._guard:
            lock = self._locks.setdefault(session_id, threading.Lock())
            self._wanted.add(session_id)
        while True:
            if not lock.acquire(blocking=False):
                return  # the holder re-checks the flag after it releases
            try:
                with self._guard:
                    self._wanted.discard(session_id)
                self._drain(session_id)
            finally:
                lock.release()
            with self._guard:
                if session_id not in self._wanted:
                    return

    def forget(self, session_id: str) -> None:
        with self._guard:
            self._wanted.discard(session_id)
            lock = self._locks.get(session_id)
            if lock is not None and not lock.locked():
                del self._locks[session_id]


def queue_item_out(item: QueueItem) -> dict[str, Any]:
    """Public view deliberately omits the stored request and its potentially sensitive message."""
    result = item.as_dict()
    result.pop("request")
    return result
