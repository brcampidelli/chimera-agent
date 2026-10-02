"""The undo offers of editing turns, kept per conversation.

Each editing turn mints a single-use token that puts its files back (`WorkspaceGuard.restore_change`).
They used to live in one module-level dict capped at 8 for the whole app, so after eight editing turns
anywhere, an older conversation's Undo button and a finished background work's undo both answered
"nothing to undo". With several conversations working at once that is minutes, not days.

Now each conversation keeps its own most recent offers, and the app keeps a total, oldest first out, so
memory stays bounded. Offers still die with the process: an undo is for the change you just watched.
"""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict
from typing import Any

#: An editing conversation's most recent offers. Enough for a long session of small turns.
PER_SESSION = 20
#: Every offer in the app. Each holds only the files its turn changed, so this is a bound, not a budget.
TOTAL = 200


class UndoOffers:
    def __init__(self, *, per_session: int = PER_SESSION, total: int = TOTAL) -> None:
        self.per_session = per_session
        self.total = total
        self._lock = threading.Lock()
        # token -> (session, value), oldest first.
        self._offers: OrderedDict[str, tuple[str, Any]] = OrderedDict()

    def offer(self, session_id: str, value: Any) -> str:
        """Keep ``value`` under a new single-use token for this conversation, and return the token."""
        token = uuid.uuid4().hex
        with self._lock:
            self._offers[token] = (session_id, value)
            mine = [t for t, (s, _) in self._offers.items() if s == session_id]
            for old in mine[: max(0, len(mine) - self.per_session)]:
                self._offers.pop(old, None)
            while len(self._offers) > self.total:
                self._offers.popitem(last=False)
        return token

    def take(self, token: str) -> Any | None:
        """The offer behind ``token``, once; None for an unknown, taken or evicted one."""
        with self._lock:
            entry = self._offers.pop(token, None)
        return None if entry is None else entry[1]
