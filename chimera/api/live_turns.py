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
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import TypeVar

T = TypeVar("T")

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


class LiveTurns:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._turns: dict[str, LiveTurn] = {}

    def start(
        self, *, turn_id: str, session_id: str, workspace: str, message: str, live_since: int
    ) -> None:
        with self._lock:
            self._turns[turn_id] = LiveTurn(
                turn_id, session_id, workspace, message, time.time(), live_since
            )

    def finish(self, turn_id: str) -> None:
        with self._lock:
            self._turns.pop(turn_id, None)

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


def _agree(before: LiveTurn | None, after: LiveTurn | None) -> bool:
    if before is None or after is None:
        # Nothing was running on one side: a turn that started or ended during the read. A turn
        # that just started has written nothing; one that just ended has left the file complete.
        return True
    return before.turn_id == after.turn_id and before.writes == after.writes and after.writes % 2 == 0
