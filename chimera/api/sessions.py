"""Durable chat sessions for the desktop API.

The messaging gateway keeps :class:`~chimera.interface.ChatSession` objects in memory only, so a
restart loses every conversation. The desktop app needs a session *list* and history that survive
restarts, so this adds a small on-disk transcript store under ``<home>/sessions/<id>.json`` plus a
manager that hydrates a live ``ChatSession`` from it and persists after each turn.

Reuses the repo's persistence convention everywhere: atomic temp-file + ``os.replace`` writes, and a
tolerant load that skips a corrupt file instead of crashing the whole store.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from chimera.interface import ChatSession
from chimera.interface.session import ChatTurn
from chimera.telemetry import get_logger

_log = get_logger("api.sessions")


@dataclass
class SessionMeta:
    """Lightweight session descriptor for the sidebar list (no full transcript)."""

    id: str
    title: str
    turns: int
    updated_at: float  # file mtime (epoch seconds); passed in, never read from the wall clock here


def _title_from_turns(turns: list[ChatTurn]) -> str:
    """A human title = the first user message, trimmed. Empty session -> 'New chat'."""
    for turn in turns:
        text = turn.user.strip().replace("\n", " ")
        if text:
            return text[:60]
    return "New chat"


class SessionStore:
    """On-disk transcript store: one JSON file per session under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, session_id: str) -> Path:
        # Guard against a hostile id escaping the store dir (path traversal / absolute path).
        name = f"{session_id}.json"
        path = (self.root / name).resolve()
        if path.parent != self.root.resolve():
            raise ValueError(f"invalid session id: {session_id!r}")
        return path

    def load(self, session_id: str) -> list[ChatTurn]:
        """Return the stored transcript, or ``[]`` if absent/unreadable (never raises on corruption)."""
        try:
            path = self._path(session_id)
        except ValueError:
            return []
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
            items = raw.get("turns", []) if isinstance(raw, dict) else []
        except (OSError, ValueError) as exc:
            _log.warning("skipping unreadable session %s: %s", session_id, exc)
            return []
        turns: list[ChatTurn] = []
        for item in items:
            if isinstance(item, dict) and "user" in item and "assistant" in item:
                turns.append(ChatTurn(user=str(item["user"]), assistant=str(item["assistant"])))
        return turns

    def save(self, session_id: str, turns: list[ChatTurn]) -> None:
        """Atomically persist a session's transcript (temp + os.replace, unique temp name)."""
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "id": session_id,
            "title": _title_from_turns(turns),
            "turns": [{"user": t.user, "assistant": t.assistant} for t in turns],
        }
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)

    def delete(self, session_id: str) -> bool:
        """Delete a session file. Returns True if it existed."""
        try:
            path = self._path(session_id)
        except ValueError:
            return False
        if path.exists():
            path.unlink()
            return True
        return False

    def list(self) -> list[SessionMeta]:
        """All stored sessions, newest first. A corrupt file is skipped, not fatal."""
        if not self.root.exists():
            return []
        metas: list[SessionMeta] = []
        for path in self.root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8") or "{}")
            except (OSError, ValueError):
                continue
            if not isinstance(raw, dict):
                continue
            turns = raw.get("turns", [])
            metas.append(
                SessionMeta(
                    id=path.stem,
                    title=str(raw.get("title") or "New chat"),
                    turns=len(turns) if isinstance(turns, list) else 0,
                    updated_at=path.stat().st_mtime,
                )
            )
        metas.sort(key=lambda m: m.updated_at, reverse=True)
        return metas


class SessionManager:
    """Live ``ChatSession`` cache backed by a :class:`SessionStore`.

    A session is built from the injected ``factory`` (the real agent stack) the first time it is
    touched, hydrated with any persisted transcript, and re-persisted after each turn via
    :meth:`persist`. This keeps the durable store and the in-memory conversation in sync without
    changing ``ChatSession`` itself.
    """

    def __init__(self, factory: object, store: SessionStore) -> None:
        # factory: Callable[[], ChatSession] — kept as object to avoid importing the Callable alias here.
        self._factory = factory
        self._store = store
        self._live: dict[str, ChatSession] = {}

    def new(self) -> str:
        """Mint a fresh empty session id (nothing is written until its first turn)."""
        return uuid4().hex[:12]

    def get(self, session_id: str) -> ChatSession:
        """Return the live session for ``session_id``, hydrating its transcript from disk on first use."""
        if session_id not in self._live:
            session: ChatSession = self._factory()  # type: ignore[operator]
            session.turns = self._store.load(session_id)
            self._live[session_id] = session
        return self._live[session_id]

    def persist(self, session_id: str) -> None:
        session = self._live.get(session_id)
        if session is not None:
            self._store.save(session_id, session.turns)

    def delete(self, session_id: str) -> bool:
        self._live.pop(session_id, None)
        return self._store.delete(session_id)

    def list(self) -> list[SessionMeta]:
        return self._store.list()
