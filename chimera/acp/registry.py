"""Keeping an external agent alive between turns.

A `session/prompt` is one message in a conversation the AGENT is holding. Start a fresh process per
turn and every turn is turn one: the agent re-reads the files it just read, re-derives what it just
worked out, and answers "no, the other one" by asking which one. The context that makes a coding
conversation cheap lives inside the agent, and it dies with the process.

So a connection outlives a turn and is keyed by the conversation. That makes this a resource pool,
with the two obligations a pool has: a ceiling (an agent per open conversation is an agent per open
conversation, and each is a node process with a model connection) and an eviction rule (a
conversation nobody has touched in an hour is not holding anything worth an agent).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.acp.agents import AcpAgentSpec
from chimera.acp.turn import AcpTurn
from chimera.telemetry import get_logger
from chimera.tools.write_region import WriteRegion

_log = get_logger("acp.registry")

#: How many external agents may be alive at once. Each is a child process holding a model
#: connection; the number is small because the cost is not ours to spend quietly.
MAX_LIVE = 4

#: How long a conversation may sit untouched before its agent is closed. An hour is long enough to
#: come back from a meeting and short enough that a forgotten tab does not hold a process all night.
IDLE_SECONDS = 3600.0


@dataclass(frozen=True)
class SessionKey:
    """What makes two turns the same conversation for an external agent.

    The workspace is part of it, and not for tidiness: an ACP session is rooted at a `cwd` fixed
    when it opened. Reusing it after the user switched projects would run the agent against the
    folder it was born in while the screen showed another — the failure would look like the agent
    reading files that are not there.
    """

    session_id: str
    provider: str
    workspace: str


class _Entry:
    __slots__ = ("turn", "used_at")

    def __init__(self, turn: AcpTurn) -> None:
        self.turn = turn
        self.used_at = time.monotonic()


class AcpRegistry:
    """The live agents, by conversation. Thread-safe; one instance per process."""

    def __init__(self, *, max_live: int = MAX_LIVE, idle_seconds: float = IDLE_SECONDS) -> None:
        self._entries: OrderedDict[SessionKey, _Entry] = OrderedDict()
        self._lock = threading.Lock()
        self.max_live = max_live
        self.idle_seconds = idle_seconds

    def get(
        self,
        key: SessionKey,
        spec: AcpAgentSpec,
        *,
        argv: list[str] | None = None,
        write_region: WriteRegion | None = None,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any], bool, str], None] | None = None,
        on_edit: Callable[[str, str], None] | None = None,
    ) -> AcpTurn:
        """The agent for this conversation, started if it is not already running.

        The three callbacks are re-bound on every call rather than fixed at construction: they point
        at THIS turn's SSE stream, and a connection that outlives a turn would otherwise keep
        writing into the queue of a request that has already ended.
        """
        self.evict_idle()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.turn.is_alive():
                entry.used_at = time.monotonic()
                self._entries.move_to_end(key)
                entry.turn.rebind(on_token=on_token, on_tool=on_tool, on_edit=on_edit)
                return entry.turn
            if entry is not None:
                # It died between turns — a crash, an update, a machine that slept. Replaced rather
                # than reported, because from the conversation's side this is just the next message.
                self._drop(key)

        turn = AcpTurn(
            spec,
            Path(key.workspace),
            argv=argv,
            write_region=write_region,
            on_token=on_token,
            on_tool=on_tool,
            on_edit=on_edit,
        )
        turn.start()  # outside the lock: launching an agent can take a minute on first use
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.turn.is_alive():
                # Two turns raced for the same conversation. Keep the one already installed and
                # close ours, so the registry never holds a process nobody can reach.
                turn.close()
                existing.turn.rebind(on_token=on_token, on_tool=on_tool, on_edit=on_edit)
                return existing.turn
            self._entries[key] = _Entry(turn)
            self._entries.move_to_end(key)
            over = len(self._entries) - self.max_live
            oldest = [k for k, _ in list(self._entries.items())[:over]] if over > 0 else []
        for stale in oldest:
            _log.debug("closing the least recently used agent: %s", stale.session_id)
            self.close(stale)
        return turn

    def cancel(self, key: SessionKey) -> bool:
        """Ask a running turn to stop. True when there was something to ask."""
        with self._lock:
            entry = self._entries.get(key)
        if entry is None:
            return False
        entry.turn.cancel()
        return True

    def close(self, key: SessionKey) -> None:
        with self._lock:
            entry = self._drop(key)
        if entry is not None:
            entry.turn.close()

    def close_all(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            entry.turn.close()

    def evict_idle(self) -> None:
        now = time.monotonic()
        with self._lock:
            stale = [k for k, e in self._entries.items() if now - e.used_at > self.idle_seconds]
        for key in stale:
            _log.debug("closing an idle agent: %s", key.session_id)
            self.close(key)

    def live(self) -> int:
        with self._lock:
            return len(self._entries)

    def _drop(self, key: SessionKey) -> _Entry | None:
        """Remove and return an entry. Caller holds the lock."""
        return self._entries.pop(key, None)


#: The process-wide registry. A module global for the same reason the session store is one: there is
#: one server, and two registries would each hold their own copy of a process that must be unique.
_REGISTRY: AcpRegistry | None = None
_REGISTRY_LOCK = threading.Lock()


def registry() -> AcpRegistry:
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = AcpRegistry()
        return _REGISTRY
