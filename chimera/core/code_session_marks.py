"""What the conversation list knows about each coding conversation besides its transcript.

The sidebar used to say one thing about a conversation: whether a turn was running in it. A person
with several conversations open could not tell, without opening each one, which had stopped to ask
them something, which had failed, and which had changed files they had not looked at yet. Each of
those is a FACT the server already holds for a moment — a question file, a verifier's verdict, an
edit list — and loses when the turn ends. This file keeps the part that has to outlive the turn:

* how many turns of the conversation have ended, and how the last one ended (failed, edited);
* the turn count the person had seen when their screen last showed the conversation;
* when it was archived, and when it was last brought back.

Nothing here is judged by a model. "Failed" is the workspace's own test command failing or the turn
raising, "edited" is the agent's own edit list, "seen" is the desktop saying it drew the
conversation. A state a model inferred would be a state that is sometimes wrong in a list whose
whole job is to be believed at a glance.

**Kept beside the transcripts, never inside them**, and that is load-bearing twice over. The list
orders conversations by the file's modification time, so writing "seen" into the transcript when a
person opens it would move every conversation they read to the top. And ``CodeSessionStore.save``
writes the file from the session object, which does not carry these fields, so the next turn's save
would erase an archive. One small JSON file per store, read on every call rather than cached: the
CLI and the app can both write it, and a cache is a second source of truth that drifts.

**Archiving touches nothing.** It is a timestamp in this file. The transcript, the folder, any
worktree and any share link are exactly as they were, which is what makes it safe to do
automatically and to undo with one click.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from chimera.core.filelock import atomic_write_text
from chimera.telemetry import get_logger

_log = get_logger("core.code_session_marks")


@dataclass
class ConversationMark:
    """One conversation's facts. The defaults are what a conversation with no entry has."""

    #: When it was archived, or None while it is in the list.
    archived_at: float | None = None
    #: When it was last brought back from the archive. The automatic rule measures inactivity from
    #: the later of this and the last write, or a conversation someone just unarchived would go
    #: straight back on the next look, because unarchiving writes nothing to the transcript.
    unarchived_at: float | None = None
    #: Turns of this conversation that ended since this file existed. A counter rather than the
    #: transcript's turn count, because the transcript trims its oldest turns and a count that can
    #: go down cannot say "a turn ended after you looked".
    turns_ended: int = 0
    #: ``turns_ended`` as it was when the desktop last drew this conversation.
    last_seen_turn: int = 0
    #: The last turn ended in an error, or its edits failed the workspace's test command.
    last_failed: bool = False
    #: The last turn edited files.
    last_edited: bool = False
    last_ended_at: float | None = None

    @property
    def unseen_diff(self) -> bool:
        """The last turn changed files and the person has not looked since it ended."""
        return self.last_edited and self.turns_ended > self.last_seen_turn


class CodeSessionMarks:
    """The marks of one store's conversations, in one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    # -- reading ---------------------------------------------------------------------------------

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # An unreadable file costs the badges and the archive, never the conversations: they are
            # in their own files. Starting empty is right; refusing to list would not be.
            _log.warning("conversation marks unreadable, read as empty: %s", exc)
            return {}
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, dict):
            return {}
        return {str(k): v for k, v in sessions.items() if isinstance(v, dict)}

    @staticmethod
    def _mark(entry: dict[str, Any] | None) -> ConversationMark:
        known = {f.name for f in fields(ConversationMark)}
        try:
            return ConversationMark(**{k: v for k, v in (entry or {}).items() if k in known})
        except TypeError:
            return ConversationMark()

    def get(self, session_id: str) -> ConversationMark:
        return self._mark(self._read().get(session_id))

    def all(self) -> dict[str, ConversationMark]:
        return {sid: self._mark(entry) for sid, entry in self._read().items()}

    # -- writing ---------------------------------------------------------------------------------

    def _update(self, session_id: str, change: Any) -> ConversationMark:
        """Read, change one entry, write — under the lock, so two turns ending at once both count."""
        with self._lock:
            sessions = self._read()
            mark = self._mark(sessions.get(session_id))
            change(mark)
            sessions[session_id] = asdict(mark)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(self.path, json.dumps({"sessions": sessions}))
            return mark

    def turn_ended(self, session_id: str, *, failed: bool, edited: bool, at: float) -> None:
        def change(mark: ConversationMark) -> None:
            mark.turns_ended += 1
            mark.last_failed = failed
            mark.last_edited = edited
            mark.last_ended_at = at

        self._update(session_id, change)

    def seen(self, session_id: str) -> bool:
        """The person's screen drew this conversation. Returns whether that changed anything, so a
        screen that reports every draw writes the file only when there was something unseen."""
        if not self.get(session_id).unseen_diff:
            return False

        def change(mark: ConversationMark) -> None:
            mark.last_seen_turn = mark.turns_ended

        self._update(session_id, change)
        return True

    def archive(self, session_id: str, *, at: float) -> ConversationMark:
        def change(mark: ConversationMark) -> None:
            mark.archived_at = at

        return self._update(session_id, change)

    def unarchive(self, session_id: str, *, at: float) -> ConversationMark:
        def change(mark: ConversationMark) -> None:
            mark.archived_at = None
            mark.unarchived_at = at

        return self._update(session_id, change)

    def forget(self, session_id: str) -> None:
        """Drop one conversation's marks — what deleting the conversation must also do, so nothing
        about a conversation outlives it here."""
        with self._lock:
            sessions = self._read()
            if sessions.pop(session_id, None) is None:
                return
            atomic_write_text(self.path, json.dumps({"sessions": sessions}))
