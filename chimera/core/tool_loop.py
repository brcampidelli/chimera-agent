"""Tool-loop circuit breaker (M15-A4) — an anti-stagnation signal at the *execution* layer.

OpenClaw hashes the last N tool calls and trips a breaker when the agent keeps making the same
move: identical repeats, an A-B-A-B ping-pong, or a poll that returns the same thing every time.
Chimera already has a crowding-score anti-stagnation signal at the *solution* layer (does a retry
keep failing the same way — ``chimera.evolution.stagnation``); this is the complementary signal at
the *execution* layer (is the agent loop physically spinning), so a stuck run stops burning budget
instead of grinding to ``max_steps``.

Pure and dependency-free: it observes ``(tool, args, observation)`` signatures over a sliding window
and returns a verdict. Detection is deliberately conservative — it fires on genuine repetition, not
on legitimately calling the same tool with *different* args — so a real multi-step run is untouched.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

Level = Literal["ok", "warn", "break"]


@dataclass(frozen=True)
class ToolLoopVerdict:
    """The circuit breaker's read on the last tool call."""

    level: Level
    reason: str = ""

    @property
    def tripped(self) -> bool:
        """True when the breaker says stop — the loop should end and answer with what it has."""
        return self.level == "break"


def _sig(name: str, arguments: dict[str, Any]) -> str:
    """A stable signature for a (tool, args) call — order-independent over the args."""
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(sorted(arguments.items()))
    return hashlib.sha256(f"{name}\x00{payload}".encode()).hexdigest()[:16]


def _obs_hash(observation: str | None) -> str:
    if observation is None:
        return ""
    return hashlib.sha256(observation.strip().encode("utf-8", "replace")).hexdigest()[:16]


class ToolLoopDetector:
    """Sliding-window detector for identical-repeat, ping-pong, and no-progress tool loops."""

    def __init__(
        self,
        *,
        window: int = 12,
        repeat_warn: int = 3,
        repeat_break: int = 5,
        pingpong_cycles_break: int = 3,
        stall_break: int = 4,
    ) -> None:
        self.repeat_warn = repeat_warn
        self.repeat_break = repeat_break
        self.pingpong_cycles_break = pingpong_cycles_break
        self.stall_break = stall_break
        self._names: deque[str] = deque(maxlen=window)
        self._sigs: deque[str] = deque(maxlen=window)
        self._obs: deque[str] = deque(maxlen=window)
        self._ok: deque[bool | None] = deque(maxlen=window)

    def record(
        self,
        name: str,
        arguments: dict[str, Any],
        observation: str | None = None,
        *,
        ok: bool | None = None,
    ) -> ToolLoopVerdict:
        """Record one tool call (+ its observation) and return the current loop verdict.

        ``ok`` says whether the call actually ran — false for an error and for a gate refusal. The
        loop computes that value one line before calling here and used to throw it away, so a tool
        stonewalled five times by a governance gate broke the run with the words "called with
        identical args", which reads as the model looping when the model asked, was told no, and
        asked again. The breaker still fires either way; what it says changes.

        ``None`` means the caller did not report an outcome, and the wording is then exactly what it
        was before this parameter existed.
        """
        self._names.append(name)
        self._sigs.append(_sig(name, arguments))
        self._obs.append(_obs_hash(observation))
        self._ok.append(ok)
        return self._assess()

    def _never_ran(self, mask: list[bool]) -> bool:
        """True when every call selected by ``mask`` reported failure, and at least one said so.

        Asks "did this call ever get through", not "were the last few blocked". A run with one
        success among the failures is a loop — something DID happen — and calling it a wall would be
        the same wrong attribution in the other direction.
        """
        chosen = [flag for flag, keep in zip(self._ok, mask, strict=True) if keep]
        return bool(chosen) and all(flag is False for flag in chosen)

    def _assess(self) -> ToolLoopVerdict:
        verdict = ToolLoopVerdict("ok")
        for candidate in (self._identical_repeat(), self._no_progress(), self._ping_pong()):
            if candidate.level == "break":
                return candidate  # a trip short-circuits — nothing is more severe
            if candidate.level == "warn" and verdict.level == "ok":
                verdict = candidate
        return verdict

    def _identical_repeat(self) -> ToolLoopVerdict:
        if not self._sigs:
            return ToolLoopVerdict("ok")
        last = self._sigs[-1]
        matches = [s == last for s in self._sigs]
        count = sum(matches)
        if count >= self.repeat_break:
            if self._never_ran(matches):
                return ToolLoopVerdict(
                    "break", f"{self._names[-1]} was refused or failed {count}× — nothing ran"
                )
            return ToolLoopVerdict("break", f"{self._names[-1]} called with identical args {count}×")
        if count >= self.repeat_warn:
            return ToolLoopVerdict("warn", f"{self._names[-1]} repeated {count}× with identical args")
        return ToolLoopVerdict("ok")

    def _no_progress(self) -> ToolLoopVerdict:
        """Same tool + same observation, back to back — a poll that never changes."""
        if len(self._obs) < self.stall_break or not self._obs[-1]:
            return ToolLoopVerdict("ok")
        name, obs = self._names[-1], self._obs[-1]
        run = 0
        for n, o in zip(reversed(self._names), reversed(self._obs), strict=True):
            if n == name and o == obs:
                run += 1
            else:
                break
        if run >= self.stall_break:
            tail = [i >= len(self._names) - run for i in range(len(self._names))]
            if self._never_ran(tail):
                return ToolLoopVerdict(
                    "break", f"{name} was refused or failed {run}× — nothing ran"
                )
            return ToolLoopVerdict("break", f"{name} polled {run}× with unchanged output")
        return ToolLoopVerdict("ok")

    def _ping_pong(self) -> ToolLoopVerdict:
        """A strictly alternating A-B-A-B tail over exactly two distinct call signatures."""
        alt = 0
        sigs = list(self._sigs)
        for i in range(len(sigs) - 1, 0, -1):
            if sigs[i] != sigs[i - 1]:
                alt += 1
            else:
                break
        # `alt` alternations over a 2-signature tail = alt+1 calls; a full cycle is 2 calls.
        tail = sigs[len(sigs) - alt - 1 :]
        if len(set(tail)) != 2:
            return ToolLoopVerdict("ok")
        cycles = alt // 2
        if cycles >= self.pingpong_cycles_break:
            return ToolLoopVerdict("break", f"ping-pong between two tool calls ×{cycles} cycles")
        if cycles >= 2:
            return ToolLoopVerdict("warn", f"ping-pong between two tool calls ×{cycles} cycles")
        return ToolLoopVerdict("ok")
