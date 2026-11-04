"""How a Code turn's close loop meets the conversation: the turn's end, the watch, and the fix turn.

`chimera/core/close_loop.py` decides and does the steps; this module is the seam between it and the
conversation. It owns three things the core must not know about:

* **When it runs.** Only for a foreground turn the owner's setting (or the owner's own screen override)
  allows. A turn that is a fix turn of an earlier loop (``close_loop_follow``) does not open a second
  pull request: it adds its commit to the open one and the watch follows that.
* **Where its result goes.** Every change of state is written into the stored receipt of the turn (the
  receipt is the only record that outlives the request), under the conversation's lock.
* **How the fix turn starts.** The fix turn is an ordinary queued message in the SAME conversation, so
  it runs through the normal turn machinery: its own posture, its own verification, its own
  self-check. Only the close loop's follow-up field makes it a fix turn.

The watch runs in a daemon thread, one per pull request head, and never blocks a turn.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from chimera.core.close_loop import (
    CloseLoop,
    Hooks,
    close_at_turn_end,
    fix_request,
    follow_ci,
    follow_up,
    gate_reason,
    turn_may_close,
)
from chimera.core.pull_request import gh_command

_STORE_RETRIES = 5
_STORE_RETRY_SECONDS = 5.0


class CloseLoopWiring:
    """One instance per server. Dependencies are injected so the tests can drive it without a server."""

    def __init__(
        self,
        *,
        enabled: Callable[[], bool],
        amend: Callable[[str, str, dict[str, Any]], int],
        enqueue: Callable[[str, str, dict[str, Any]], bool],
        locked: Callable[[str], AbstractContextManager[object]],
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        gh: Sequence[str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._amend = amend
        self._enqueue = enqueue
        self._locked = locked
        self._sleep = sleep
        self._clock = clock
        self._gh = gh

    def at_turn_end(
        self,
        *,
        session_id: str,
        turn_id: str,
        ws: Path,
        requested: bool | None,
        via_screen: bool,
        origin_ok: bool,
        continuation_ok: bool = False,
        message: str,
        measure: Callable[[], tuple[list[str], str]],
        verified: bool,
        self_check_clean: bool,
        follow: dict[str, Any] | None,
    ) -> CloseLoop | None:
        """The loop's record for this turn, or None when the loop is off for it (nothing is recorded then)."""
        if follow is None:
            # A loop starts only from a turn the owner's screen started (`may_start_loop`, decided by the
            # caller). A continuation (`follow`) is not a start: it exists only because a loop the screen
            # started asked for it, and the server sets the field, so a body cannot.
            if not origin_ok:
                return None
            if not turn_may_close(bool(self._enabled()), requested, via_screen):
                return None
            # Measured only now, when the loop is on: a turn with the switch off pays no diff.
            paths, incomplete = measure()
            gate = gate_reason(verified=verified, self_check_clean=self_check_clean, incomplete=incomplete)
            if gate:
                return CloseLoop(turn_id=turn_id, skipped_reason=gate)
            state = close_at_turn_end(
                ws, turn_id=turn_id, message=message, paths=paths, hooks=self._hooks(),
            )
        else:
            state = CloseLoop.from_payload(follow.get("state") or {})
            # Defence in depth: the continuation field is server-set, but a continuation still never runs
            # from the bridge, a channel or a guest, whatever writes the field.
            if not continuation_ok:
                state.ci = "failed"
                state.error = "a fix turn of the close loop runs only from the owner's own conversation"
                return state
            if not self._enabled():
                state.ci = "failed"
                state.error = "the close loop was turned off before its fix turn ran"
                return state
            paths, incomplete = measure()
            gate = gate_reason(verified=verified, self_check_clean=self_check_clean, incomplete=incomplete)
            if gate:
                state.ci, state.error = "failed", f"the fix turn did not close: {gate}"
                return state
            state = follow_up(
                ws, state=state, message=message, paths=paths, hooks=self._hooks(),
            )
        if state.ci == "pending" and state.commit:
            self._start_watch(state, ws, session_id)
        return state

    def _hooks(self) -> Hooks:
        gh = list(self._gh) if self._gh is not None else gh_command()
        return Hooks(gh=gh, sleep=self._sleep, clock=self._clock)

    def _start_watch(self, state: CloseLoop, ws: Path, session_id: str) -> None:
        # The watch owns a copy: the done frame reads `state` on this thread while the watch mutates its own.
        watched = CloseLoop.from_payload(state.to_payload())
        gh = list(self._gh) if self._gh is not None else gh_command()
        if gh is None:
            state.error = "the GitHub CLI (gh) is not installed; CI is not followed"
            state.ci = ""
            return
        hooks = Hooks(
            gh=gh,
            sleep=self._sleep,
            clock=self._clock,
            ask_fix=lambda log, attempt: self._ask_fix(session_id, ws, watched, log, attempt),
            report=lambda current: self._record(session_id, current),
        )
        head = watched.commit
        thread = threading.Thread(
            target=self._watch, args=(ws, watched, hooks, head, gh),
            name=f"close-loop-{watched.turn_id[:8]}", daemon=True,
        )
        thread.start()

    def _watch(
        self, ws: Path, state: CloseLoop, hooks: Hooks, head: str, gh: Sequence[str],
    ) -> None:
        try:
            follow_ci(ws, state, hooks, head=head, gh=gh)
        except Exception as exc:  # noqa: BLE001 - a watch must never take the server down with it
            state.error = f"the CI watch stopped: {type(exc).__name__}"
            hooks.report(state)

    def _record(self, session_id: str, state: CloseLoop) -> None:
        payload = state.to_payload()
        for _ in range(_STORE_RETRIES):
            # Under the conversation's lock: a turn's save must not interleave with this rewrite.
            with self._locked(session_id):
                if self._amend(session_id, state.turn_id, payload) > 0:
                    return
            # The turn that owns the receipt may not have stored it yet; try again shortly.
            self._sleep(_STORE_RETRY_SECONDS)

    def _ask_fix(
        self, session_id: str, ws: Path, state: CloseLoop, log: str, attempt: int,
    ) -> bool:
        payload = {
            "message": fix_request(log, attempt),
            "session_id": session_id,
            "workspace": str(ws),
            "close_loop_follow": {"state": state.to_payload(), "turn_id": state.turn_id},
        }
        return self._enqueue(session_id, f"close-loop-{state.turn_id}-{attempt}", payload)

