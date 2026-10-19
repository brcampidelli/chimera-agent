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
from typing import Literal, TypeVar

T = TypeVar("T")

_log = logging.getLogger(__name__)

#: How many times a reader retries when a write lands under it. A save is milliseconds and a write
#: needs two of them to disagree with one read, so a third disagreement is not a race but a turn
#: saving in a loop, and the last look is then as good an answer as there is.
READ_ATTEMPTS = 3

#: What a stop reached. ``stopped_before_start``: the turn was still queued for its folder, so it
#: is out of the running list already and will never start. ``stopping``: the turn is working, and
#: its loop ends at the next step boundary. ``not_running``: no such turn.
StopOutcome = Literal["stopped_before_start", "stopping", "not_running"]


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
        # Turns queued behind another turn or run in their folder (C11, study 34). A stop that
        # finds a turn here takes it out of the running list on the spot: it has no step in
        # progress to finish, and the turn checks the same set before it starts (`claim_start`).
        self._waiting: set[str] = set()

    def start(
        self, *, turn_id: str, session_id: str, workspace: str, message: str, live_since: int,
        started_at: float | None = None,
    ) -> None:
        # ``started_at`` lets the caller register the clock its opening frame already announced,
        # so the turn's timer and the running list agree to the millisecond.
        with self._lock:
            self._turns[turn_id] = LiveTurn(
                turn_id, session_id, workspace, message,
                time.time() if started_at is None else started_at, live_since,
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
            self._waiting.discard(turn_id)

    def mark_waiting(self, turn_id: str) -> None:
        """The turn is queued for its folder. A turn that is not registered is a no-op."""
        with self._lock:
            if turn_id in self._turns:
                self._waiting.add(turn_id)

    def is_waiting(self, turn_id: str) -> bool:
        with self._lock:
            return turn_id in self._waiting

    def claim_start(self, turn_id: str) -> bool:
        """The turn got its folder: may it start? False when a stop reached it first.

        Under the same lock as :meth:`stop`, so the two cannot interleave: either the stop saw a
        waiting turn and answered "stopped before it started" — and this returns False — or this
        ran first and the stop is a running turn's stop. Without it, a folder freed in the same
        instant as the stop could let a turn the person was told had been taken out of the queue
        run anyway. A turn that is not registered (a background work's) is never refused here.
        """
        with self._lock:
            self._waiting.discard(turn_id)
            event = self._stops.get(turn_id)
            return event is None or not event.is_set()

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
            steering = self._open_steering(turn_id)
            if steering is None:
                return "not_running"
            if not steering.reading:
                return "not_steerable"
            steering.pending.append(text)
            return "queued"

    def _open_steering(self, turn_id: str) -> _Steering | None:
        """The turn's steering while the turn is RUNNING — registered and its loop not returned —
        else None. The one definition of "running" that :meth:`guide` and :meth:`is_running` share
        (lock held by the caller)."""
        steering = self._steering.get(turn_id)
        if turn_id not in self._turns or steering is None or steering.closed:
            return None
        return steering

    def is_running(self, turn_id: str) -> bool:
        """Whether the turn is still working: the answer guidance and the bridge's job status both
        give, from this one place.

        They used to ask different questions. The job status asked whether the turn was REGISTERED
        (``get``), which stays true while the turn saves its transcript after its loop returned;
        guidance asked whether the loop was still OPEN. In that window the bridge said
        ``turn_running: true`` and the guidance route said "that turn is not running any more"
        (study 34, item 14).
        """
        with self._lock:
            return self._open_steering(turn_id) is not None

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
        return self.stop(turn_id) != "not_running"

    def stop(self, turn_id: str) -> StopOutcome:
        """Ask a turn to stop, and say what that reached (:data:`StopOutcome`).

        A turn still queued for its folder leaves the running list here, before this returns: it
        never started, so there is nothing to wait for, and a list that went on showing it as
        running after "stopped" is what made a queued turn look like one that ignored Stop (C11).
        Its thread still ends itself (it notices the signal within one wait interval, and
        :meth:`claim_start` refuses it if the folder came free first); :meth:`finish` then clears
        what is left.
        """
        with self._lock:
            event = self._stops.get(turn_id)
            if event is None or turn_id not in self._turns:
                return "not_running"
            event.set()
            queued = turn_id in self._waiting
            if queued:
                self._waiting.discard(turn_id)
                self._turns.pop(turn_id, None)
                self._steering.pop(turn_id, None)
            cancels = list(self._cancels.get(turn_id, ()))
        for cancel in cancels:
            _run_cancel(cancel)
        return "stopped_before_start" if queued else "stopping"

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
