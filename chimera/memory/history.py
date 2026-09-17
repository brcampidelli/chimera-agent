"""What was asked and answered in every coding conversation — kept after the conversation forgets.

A code session (:class:`chimera.core.code_session.CodeSessionStore`) keeps the model's own message
list, trimmed to ``DEFAULT_MAX_MESSAGES`` at a ``user`` boundary — about thirty tool-using turns.
That is the right bound for a transcript that is re-sent to a provider on every turn, and the wrong
bound for a question like *"what did we decide about the login function two weeks ago?"*: the turn
that decided it is the first one trimming drops, and a search over the session files finds nothing,
not because it was never said but because the file no longer holds it.

So this is an append-only index of **completed turns**: the person's message, the answer, the files
the turn read or edited, when it happened — one row per turn, in one SQLite file under the home,
with an FTS5 index (and the same ``LIKE`` degradation :mod:`chimera.memory.sqlite_store` documents
when FTS5 is not compiled in). Scoped by project the way memory is (:func:`project_key`), and
searched through the same tokenizer and the same function-word list, so *"o que é isso?"* recalls
nothing here for the reason it recalls nothing there.

It is not the memory store. A memory is a fact the agent chose to keep; a history row is a record of
a turn that happened, whether or not anything in it was worth keeping — and it is written by the
turn's own finishing code, never by the model. ``recall_history`` says which of the two it searched.

Three things are deliberate:

* **Written through ``redact``**, as the session file is and for the same reason: a credential the
  person pasted into the chat outlives the session here, and an index of one's own conversations
  must not be the place a key is found in clear.
* **A turn that ran tainted stays marked.** Its answer may carry text an untrusted page put there,
  and recalled two weeks later it would read as the agent's own conclusion. The row keeps the
  flag, and the tool prints it in-line the way a tainted memory is labelled on recall.
* **Deleting a conversation deletes its rows.** ``Clear`` on the screen says the conversation is
  gone; an index that still answered questions about it would make that a lie.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.core.redact import redact
from chimera.memory.models import EVERY_PROJECT
from chimera.telemetry import get_logger

_log = get_logger("memory.history")

#: The file under the home. One per home: every project's turns, scoped by the ``project`` column.
HISTORY_FILE = "history.db"

#: How much of a message or an answer is kept. A turn's message can carry an attached document's
#: whole text, and an answer can be long; the index is for finding the turn, and fifty thousand
#: characters of either is more than any excerpt will show.
TEXT_CAP = 50_000

#: The columns, in the one order every SELECT and INSERT uses.
_COLUMNS = "turn_id, session_id, project, asked_at, tainted, asked, answered, files, edited, tools"


@dataclass(frozen=True)
class HistoryHit:
    """One recorded turn, as the search returns it."""

    turn_id: str
    session_id: str
    project: str
    asked_at: float
    tainted: bool
    asked: str
    answered: str
    files: list[str]
    edited: list[str]
    tools: list[str]


def _split(raw: Any) -> list[str]:
    return [p for p in str(raw or "").split("\n") if p]


def _join(items: list[str]) -> str:
    # Newline-separated, because a path can contain a space and a newline is the one character a
    # path the tools accept never does. Deduplicated in order: the same file read three times is
    # one file the turn touched.
    seen: list[str] = []
    for item in items:
        text = str(item).replace("\n", " ").strip()
        if text and text not in seen:
            seen.append(text)
    return "\n".join(seen)


class HistoryIndex:
    """The append-only index of completed coding turns, in SQLite under ``home``."""

    def __init__(self, home: Path) -> None:
        self.path = Path(home) / HISTORY_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by the turn that records and the tool that searches, which run on
        # different threads: the request's worker thread writes, the agent's own thread reads. A
        # lock around every statement is what makes that sharing safe; `check_same_thread` only
        # forbids it.
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._lock = threading.Lock()
        self._fts = self._init_schema()

    # -- schema ------------------------------------------------------------------------------

    def _init_schema(self) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS turns USING fts5("
                    "turn_id UNINDEXED, session_id UNINDEXED, project UNINDEXED, "
                    "asked_at UNINDEXED, tainted UNINDEXED, asked, answered, files, "
                    "edited UNINDEXED, tools UNINDEXED)"
                )
                self._conn.commit()
                return True
            except sqlite3.OperationalError:  # FTS5 not compiled in — a plain table and LIKE
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS turns ("
                    "turn_id TEXT PRIMARY KEY, session_id TEXT, project TEXT, asked_at REAL, "
                    "tainted INTEGER, asked TEXT, answered TEXT, files TEXT, edited TEXT, "
                    "tools TEXT)"
                )
                self._conn.commit()
                return False

    @property
    def full_text(self) -> bool:
        """Whether FTS5 is doing the searching (``False`` means the ``LIKE`` fallback)."""
        return self._fts

    # -- writing -----------------------------------------------------------------------------

    def record(
        self,
        *,
        turn_id: str,
        session_id: str,
        project: str | None,
        asked: str,
        answered: str,
        files: list[str] | None = None,
        edited: list[str] | None = None,
        tools: list[str] | None = None,
        tainted: bool = False,
        asked_at: float | None = None,
    ) -> None:
        """Keep one completed turn. Recording the same ``turn_id`` again replaces the row.

        Never raises on the turn's behalf: a record ABOUT a turn that already happened and was
        already paid for must not be able to fail the turn, so a database that will not take the
        row is logged and the turn goes on. (The caller catches too; this is the second net.)
        """
        row = (
            turn_id,
            session_id,
            project or "",
            float(asked_at if asked_at is not None else time.time()),
            1 if tainted else 0,
            redact(asked[:TEXT_CAP]),
            redact(answered[:TEXT_CAP]),
            _join(files or []),
            _join(edited or []),
            _join(tools or []),
        )
        try:
            with self._lock:
                self._conn.execute("DELETE FROM turns WHERE turn_id = ?", (turn_id,))
                self._conn.execute(
                    f"INSERT INTO turns ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            _log.warning("history: turn %s not recorded: %s", turn_id, exc)

    def forget_session(self, session_id: str) -> int:
        """Drop every turn of one conversation. Returns how many rows went."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM turns WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return int(cursor.rowcount or 0)

    def forget_sessions(self, session_ids: list[str]) -> int:
        return sum(self.forget_session(sid) for sid in session_ids)

    # -- reading -----------------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        project: str | None = EVERY_PROJECT,
        k: int = 5,
        since: float | None = None,
    ) -> list[HistoryHit]:
        """The turns whose message, answer or files match ``query``, best match first.

        ``project`` scopes the CANDIDATES in SQL, for the reason the memory store gives: a LIMIT
        applied before the filter returns k rows of other projects while this project's sit below
        the cut. :data:`EVERY_PROJECT` searches every project; a path searches that one; ``None``
        (a turn with no folder) searches the rows recorded with none.

        ``since`` is an epoch: only turns asked at or after it. ``k`` is clamped to 1..50.
        """
        from chimera.memory.tokens import informative, tokens

        terms = sorted(informative(tokens(query)))
        if not terms:
            return []
        k = max(1, min(int(k), 50))

        scope, scope_params = self._scope(project)
        if since is not None:
            scope += " AND asked_at >= ?"
            scope_params.append(float(since))

        rows: list[tuple[Any, ...]] = []
        if self._fts:
            # Each term quoted: a phrase of one token, tokenized by FTS5 exactly as the content
            # was, so `login_user` in a query reaches `login_user` in a file name. Column-filtered
            # to the three text columns, so a tool name or a session id never matches a word.
            match = "{asked answered files} : (" + " OR ".join(
                '"' + term.replace('"', '""') + '"' for term in terms
            ) + ")"
            try:
                with self._lock:
                    rows = self._conn.execute(
                        f"SELECT {_COLUMNS} FROM turns WHERE turns MATCH ?{scope} "
                        "ORDER BY rank, asked_at DESC LIMIT ?",
                        (match, *scope_params, k),
                    ).fetchall()
            except sqlite3.OperationalError:
                rows = []  # a query FTS5 will not parse — LIKE below still answers
            # Nothing from FTS5 falls through to LIKE for the reason the memory store gives: its
            # tokenizer keeps a run of Han as one token while `tokens` splits it per character,
            # and a substring match is the one that can still find it.
        if not rows:
            clause = " OR ".join(
                "(lower(asked) LIKE ? OR lower(answered) LIKE ? OR lower(files) LIKE ?)"
                for _ in terms
            )
            params: list[object] = []
            for term in terms:
                params.extend([f"%{term}%"] * 3)
            params.extend(scope_params)
            params.append(k)
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM turns WHERE ({clause}){scope} "
                    "ORDER BY asked_at DESC LIMIT ?",
                    params,
                ).fetchall()
        return [self._to_hit(row) for row in rows]

    def recent(self, *, project: str | None = EVERY_PROJECT, k: int = 5) -> list[HistoryHit]:
        """The newest turns, no query — what the tool lists when asked for nothing in particular."""
        scope, scope_params = self._scope(project)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {_COLUMNS} FROM turns WHERE 1=1{scope} ORDER BY asked_at DESC LIMIT ?",
                (*scope_params, max(1, min(int(k), 50))),
            ).fetchall()
        return [self._to_hit(row) for row in rows]

    def count(self, *, project: str | None = EVERY_PROJECT) -> int:
        scope, scope_params = self._scope(project)
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM turns WHERE 1=1{scope}", scope_params
            ).fetchone()
        return int(row[0]) if row else 0

    def __len__(self) -> int:
        return self.count()

    @staticmethod
    def _scope(project: str | None) -> tuple[str, list[object]]:
        if project == EVERY_PROJECT:
            return "", []
        return " AND project = ?", [project or ""]

    @staticmethod
    def _to_hit(row: tuple[Any, ...]) -> HistoryHit:
        return HistoryHit(
            turn_id=str(row[0]),
            session_id=str(row[1]),
            project=str(row[2] or ""),
            asked_at=float(row[3] or 0.0),
            tainted=bool(int(row[4] or 0)),
            asked=str(row[5] or ""),
            answered=str(row[6] or ""),
            files=_split(row[7]),
            edited=_split(row[8]),
            tools=_split(row[9]),
        )


def files_of_exchange(exchange: dict[str, Any]) -> list[str]:
    """The paths a folded exchange's tool calls named — a ``path`` argument, in call order.

    Read off the same fold the replay endpoint shows (:func:`chimera.api.code_replay.
    exchanges_from_messages`), so the files the index says a turn touched are the files the screen
    shows it touching. Only ``path``: the one argument name every file tool shares.
    """
    out: list[str] = []
    for call in exchange.get("tools") or []:
        if not isinstance(call, dict):
            continue
        arguments = call.get("arguments")
        path = arguments.get("path") if isinstance(arguments, dict) else None
        if isinstance(path, str) and path.strip():
            out.append(path.strip())
    return out


_INDEXES: dict[str, HistoryIndex] = {}
_INDEXES_LOCK = threading.Lock()


def history_for(home: Path) -> HistoryIndex:
    """One index per home per process — the turn that records and the tool that searches share
    the connection, the same way the job registry is shared."""
    key = str(Path(home).resolve())
    with _INDEXES_LOCK:
        index = _INDEXES.get(key)
        if index is None:
            index = HistoryIndex(Path(home))
            _INDEXES[key] = index
        return index
