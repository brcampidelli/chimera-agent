"""Which coding turns are running right now, and whether their transcript is on disk yet.

A turn keeps running on the server when the screen that started it goes away, and its frames are
kept, so nothing is lost by leaving. What was missing was the way back: a conversation is stored
when the agent finishes, not while it works, so a screen that reopened a session mid-turn read an
empty file, and the conversation was not in the list either. Nothing said "there is a turn running
here", so nobody could follow it.

This is that statement, and only that. It lives in memory on purpose: a turn is a thread of this
process, so a restart ends every turn, and a record on disk would go on saying "running" for work
that no longer exists.

The one subtle thing is the window after the agent finishes. The transcript is saved as soon as the
agent's run returns, but the verification and the receipt come after it, and the turn only ends
when they are done, which can be minutes. In that window the file already holds the exchange AND
the turn is still running, so a reader that showed both would draw the same exchange twice. So each
turn counts its own transcript writes, odd while one is happening and even between them, and a
reader that must be exact reads the file between two looks at the count and trusts it only when
both looks agree (`read_consistently`). A lock around the file read would be simpler and worse: the
read can call the network, and the turn's own save would wait on it.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from typing import TypeVar

T = TypeVar("T")

_log = logging.getLogger(__name__)

#: How many times a reader retries when a write lands under it. A save is milliseconds and a write
#: needs two of them to disagree with one read, so a third disagreement is not a race but a turn
#: saving in a loop, and the last look is then as good an answer as there is.
READ_ATTEMPTS = 3


@dataclass(frozen=True)
class LiveTurn:
    turn_id: str
    session_id: str
    workspace: str
    message: str
    started_at: float
    #: The bus sequence before this turn's opening frame. A screen that asks the conversation's live
    #: stream for everything after it gets the whole turn, opening frame first.
    live_since: int
    #: Transcript writes so far, times two: odd while one is in progress. See the module docstring.
    writes: int = 0

    @property
    def transcript_saved(self) -> bool:
        """A complete transcript write happened, so the stored file already holds this turn."""
        return self.writes >= 2


@dataclass
class _Steering:
    pending: list[str] = field(default_factory=list)
    read: list[tuple[str, float]] = field(default_factory=list)
    #: The loop polled at least once, so text queued now WILL be read (unless the loop ends first).
    reading: bool = False
    #: The loop has returned: nothing more will be read.
    closed: bool = False


class LiveTurns:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: dict[str, LiveTurn] = {}
        # One stop signal per running turn, and what else a stop must reach (an external agent's
        # prompt). The Stop button used to abort only the screen's request, so a "stopped" turn went
        # on calling the model, editing files and spending on the server until it ended by itself.
        self._stops: dict[str, threading.Event] = {}
        self._cancels: dict[str, list[Callable[[], object]]] = {}
        # Guidance (S30-66): what the owner typed while a turn ran, per turn. See `guide`.
        self._steering: dict[str, _Steering] = {}

    def start(
        self, *, turn_id: str, session_id: str, workspace: str, message: str, live_since: int
    ) -> None:
        with self._lock:
            self._turns[turn_id] = LiveTurn(
                turn_id, session_id, workspace, message, time.time(), live_since
            )
            self._stops[turn_id] = threading.Event()
            self._cancels[turn_id] = []
            self._steering[turn_id] = _Steering()

    def finish(self, turn_id: str) -> None:
        with self._lock:
            self._turns.pop(turn_id, None)
            self._stops.pop(turn_id, None)
            self._cancels.pop(turn_id, None)
            self._steering.pop(turn_id, None)

    def should_stop(self, turn_id: str) -> Callable[[], bool]:
        """What the agent loop polls once per step. A turn that is not registered (a background
        work's, which the works registry stops) never reads as stopped here."""
        with self._lock:
            event = self._stops.get(turn_id)
        return event.is_set if event is not None else (lambda: False)

    def on_stop(self, turn_id: str, cancel: Callable[[], object]) -> None:
        """Run ``cancel`` when this turn is asked to stop, or at once if it already was: the stop
        may land between the turn starting and the external agent being reached."""
        with self._lock:
            event = self._stops.get(turn_id)
            if event is not None and not event.is_set():
                self._cancels[turn_id].append(cancel)
                return
        if event is not None:
            _run_cancel(cancel)

    def guide(self, turn_id: str, text: str) -> str:
        """Queue owner-authored ``text`` for a running turn; what happened, as one word.

        ``queued`` — the agent reads it at its next step boundary. ``not_running`` — no such turn,
        or its loop has already ended. ``not_steerable`` — the turn is running but its loop has not
        asked for guidance (yet, or ever: an external agent's turn never does). Refused rather than
        accepted-and-dropped: a person told "queued" for text no model will read was misled.
        """
        with self._lock:
            steering = self._steering.get(turn_id)
            if steering is None or steering.closed:
                return "not_running"
            if not steering.reading:
                return "not_steerable"
            steering.pending.append(text)
            return "queued"

    def take_guidance(self, turn_id: str) -> Callable[[], list[str]]:
        """What the agent loop polls at each step boundary: the guidance queued since the last poll.

        The first poll is what opens the turn to guidance, so a turn whose loop never polls (an
        external agent, a backend without the parameter) never says "queued". What is taken is
        recorded with the time it was READ, which is what the receipt reports: text the model saw,
        not text somebody sent.
        """

        def take() -> list[str]:
            with self._lock:
                steering = self._steering.get(turn_id)
                if steering is None or steering.closed:
                    return []
                steering.reading = True
                items, steering.pending = steering.pending, []
                now = time.time()
                steering.read.extend((text, now) for text in items)
                return items

        return take

    def close_guidance(self, turn_id: str) -> tuple[list[dict[str, object]], list[str]]:
        """Refuse guidance from now on; return what was read, as receipt entries, and what was not.

        Called the moment the agent's loop returns. Something can be queued after the loop's last
        poll and before this — a few milliseconds — and it is returned as unread, for the receipt to
        say so, instead of being reported as delivered.
        """
        with self._lock:
            steering = self._steering.get(turn_id)
            if steering is None:
                return [], []
            steering.closed = True
            unread, steering.pending = steering.pending, []
            read = [
                # A user turn of the conversation, typed by the owner through the owner's guarded
                # route: owner input, never tainted — the taint ledger is about what tools brought
                # in, and this came from the person the turn works for.
                {"role": "user", "author": "owner", "text": text, "read_at": at, "tainted": False}
                for text, at in steering.read
            ]
            return read, unread

    def request_stop(self, turn_id: str) -> bool:
        """Ask a running turn to stop. False when there is no such running turn."""
        with self._lock:
            event = self._stops.get(turn_id)
            cancels = list(self._cancels.get(turn_id, ()))
        if event is None:
            return False
        event.set()
        for cancel in cancels:
            _run_cancel(cancel)
        return True

    @contextlib.contextmanager
    def writing(self, turn_id: str) -> Iterator[None]:
        """Around a save of this turn's transcript. A turn that is not registered is a no-op, which
        is what a background work's turn is: it has its own session and nobody follows it here."""
        self._bump(turn_id)
        try:
            yield
        finally:
            self._bump(turn_id)

    def _bump(self, turn_id: str) -> None:
        with self._lock:
            turn = self._turns.get(turn_id)
            if turn is not None:
                self._turns[turn_id] = replace(turn, writes=turn.writes + 1)

    def running(self) -> list[LiveTurn]:
        """Every turn in flight, oldest first."""
        with self._lock:
            return sorted(self._turns.values(), key=lambda t: t.started_at)

    def get(self, turn_id: str) -> LiveTurn | None:
        with self._lock:
            return self._turns.get(turn_id)

    def of_session(self, session_id: str) -> LiveTurn | None:
        for turn in self.running():
            if turn.session_id == session_id:
                return turn
        return None

    def read_consistently(
        self, session_id: str, read: Callable[[], T]
    ) -> tuple[T, LiveTurn | None]:
        """``read()``, and the session's running turn as it stood while ``read`` ran.

        Retried while a transcript write overlaps the read, because then the file and the flag can
        disagree about whether the turn's exchange is on disk.
        """
        attempt = 0
        while True:
            attempt += 1
            before = self.of_session(session_id)
            value = read()
            after = self.of_session(session_id)
            if _agree(before, after) or attempt >= READ_ATTEMPTS:
                return value, after
            time.sleep(0.01)


def _run_cancel(cancel: Callable[[], object]) -> None:
    # A cancel that fails must not keep the stop from reaching the agent loop, which is what ends
    # the turn; the failure is logged, not raised into the request that pressed Stop.
    try:
        cancel()
    except Exception:  # noqa: BLE001
        _log.warning("cancelling a turn's external agent failed", exc_info=True)


def _agree(before: LiveTurn | None, after: LiveTurn | None) -> bool:
    if before is None or after is None:
        # Nothing was running on one side: a turn that started or ended during the read. A turn
        # that just started has written nothing; one that just ended has left the file complete.
        return True
    return before.turn_id == after.turn_id and before.writes == after.writes and after.writes % 2 == 0
