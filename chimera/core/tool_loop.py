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

    def record(
        self, name: str, arguments: dict[str, Any], observation: str | None = None
    ) -> ToolLoopVerdict:
        """Record one tool call (+ its observation) and return the current loop verdict."""
        self._names.append(name)
        self._sigs.append(_sig(name, arguments))
        self._obs.append(_obs_hash(observation))
        return self._assess()

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
        count = sum(1 for s in self._sigs if s == last)
        if count >= self.repeat_break:
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
