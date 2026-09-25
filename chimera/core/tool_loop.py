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

**A spin repeats the call AND the answer** (study 24, 2026-09-24). Until then each rule looked at half
of that pair, and each half had its own measured false alarm:

- ``_identical_repeat`` looked at the args only. Five ``scroll`` calls each returned a new viewport and
  still stopped the run: all 20 failures viewport-first added in ``bench/browser_viewport_tasks``.
- ``_no_progress`` looked at the output only. Four different edits all answer ``edited x: replaced 1
  occurrence``, and that stopped the run mid-work: **all 20** breaker trips of the strong executor in
  ``bench/tool_loop_fork`` were four successful, distinct edits.

Now a repeat must match the last call's args and its answer (numbers and spacing flattened, so a
timing in a test report does not hide a real loop). A run of unchanged output with *different* args
still breaks when those calls failed — the same error four times is a wall, whatever was tried.

**A call that changes only its shape asks the same question.** ``list_dir`` of the root at depth 2,
3, 2 and 1 returned the same listing each time. It was the fix's one known miss. ``_no_progress``
therefore also compares ``_target_sig``, the call without its numbers, booleans and path spellings,
against the exact answer. It was replayed on about 2,500 recorded traces
(``bench/tool_loop_near_args``). Of the 138 legacy stops, the wider rule reproduces 5, all
re-listings of the root. It reproduces none of the 86 distinct-write stops above, and it would
have stopped none of the 76 runs that continued under the fixed rule.

**A failure the tool reports in its own answer is a failure.** `[exit 127]` from a command passed as a
list, or the same `ZeroDivisionError` from four pieces of code, reached the loop as successes, so
"the same failure under different args" never stopped them (``_answered_failure``). The same replay
(``bench/tool_loop_silent_failure``) finds that rule stops exactly those three legacy runs, none of the
86 distinct-write stops, and none of the 76 continuing runs.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
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


_NUMBER = re.compile(r"\s*-?\d+(?:\.\d+)?\s*")
_PATH_KEY = re.compile(r"(?:^|_)(?:path|file|filename|dir|directory|folder|cwd)s?$", re.IGNORECASE)


def _norm_path(value: str) -> str:
    """``""``, ``"."``, ``"./"`` and ``"in/"`` versus ``"in"``: the same place, spelled differently."""
    v = value.strip().replace("\\", "/")
    return posixpath.normpath(v) if v else "."


def _target_sig(name: str, arguments: dict[str, Any]) -> str:
    """The call without its shape: what it points at, not how much of it it asks for.

    ``list_dir`` of one folder at depth 2, 3 and 1, or with and without ``max_results``, asks one
    question four times. So this drops numbers and booleans (depth, limit, offset, timeout), and any
    string that is only a number, since a model will send depth ``"0"``. It also normalises
    path-like keys. Every other value is kept whole, including strings, lists and objects, so four
    edits with different text stay four calls.
    """
    kept: dict[str, Any] = {}
    for key, value in arguments.items():
        if value is None or isinstance(value, bool | int | float):
            continue
        if isinstance(value, str):
            if _PATH_KEY.search(str(key)):
                value = _norm_path(value)
            elif _NUMBER.fullmatch(value):
                continue
        kept[key] = value
    return _sig(name, kept)


def _obs_hash(observation: str | None) -> str:
    if observation is None:
        return ""
    return hashlib.sha256(observation.strip().encode("utf-8", "replace")).hexdigest()[:16]


_EXIT = re.compile(r"\A\[exit (-?\d+)\]\n?(.*)\Z", re.DOTALL)
_EXCEPTION_LINE = re.compile(r"[A-Z]\w*(?:Error|Exception): .+")


def _answered_failure(observation: str | None) -> bool:
    """A failure the tool reported in its answer rather than with the ``error:`` prefix.

    The loop's ``ok`` is false for an error or a refusal. A command that ran and failed is a success
    to it. ``run_shell`` and ``execute_code`` answer ``[exit N]`` and the output;
    ``code_interpreter`` answers the exception's last line. Measured in ``bench/tool_loop_near_args``:
    ``run_shell`` given its command as a list answered ``[exit 127] /bin/sh: 1: [bash,: not found``
    whatever the command, and a ``ZeroDivisionError`` came back four times. Both times the same
    failure under different args, which the breaker is meant to stop. A non-zero exit with no text
    is not counted: that is a ``grep`` or a ``test`` that found nothing, and four searches that find
    nothing are exploring.
    """
    if not observation:
        return False
    exited = _EXIT.match(observation.strip())
    if exited:
        return exited.group(1) != "0" and bool(exited.group(2).strip())
    last = observation.rstrip().rsplit("\n", 1)[-1]
    return bool(_EXCEPTION_LINE.fullmatch(last))


_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def _gist_hash(observation: str | None) -> str:
    """The observation with its numbers and spacing flattened: the same test failing twice in 0.52s and
    0.53s is the same answer, and a new viewport or a new file listing is not."""
    if observation is None:
        return ""
    gist = _SPACE.sub(" ", _DIGITS.sub("#", observation)).strip()
    return hashlib.sha256(gist.encode("utf-8", "replace")).hexdigest()[:16]


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
        self._targets: deque[str] = deque(maxlen=window)
        self._obs: deque[str] = deque(maxlen=window)
        self._gist: deque[str] = deque(maxlen=window)
        self._ok: deque[bool | None] = deque(maxlen=window)
        self._failed: deque[bool] = deque(maxlen=window)

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
        self._targets.append(_target_sig(name, arguments))
        self._obs.append(_obs_hash(observation))
        self._gist.append(_gist_hash(observation))
        self._ok.append(ok)
        self._failed.append(ok is False or _answered_failure(observation))
        return self._assess()

    def _never_ran(self, mask: list[bool]) -> bool:
        """True when every call selected by ``mask`` reported failure, and at least one said so.

        Asks "did this call ever get through", not "were the last few blocked". A run with one
        success among the failures is a loop — something DID happen — and calling it a wall would be
        the same wrong attribution in the other direction.
        """
        chosen = [flag for flag, keep in zip(self._ok, mask, strict=True) if keep]
        return bool(chosen) and all(flag is False for flag in chosen)

    def _same_call(self) -> list[bool]:
        """Which calls in the window had the last call's tool and args, whatever they returned."""
        last = self._sigs[-1]
        return [s == last for s in self._sigs]

    def _assess(self) -> ToolLoopVerdict:
        verdict = ToolLoopVerdict("ok")
        for candidate in (self._identical_repeat(), self._no_progress(), self._ping_pong()):
            if candidate.level == "break":
                return candidate  # a trip short-circuits — nothing is more severe
            if candidate.level == "warn" and verdict.level == "ok":
                verdict = candidate
        return verdict

    def _identical_repeat(self) -> ToolLoopVerdict:
        """The same call returning the same answer. A repeat whose answer changed is not a spin: the
        tool did something new (a scroll, a queue read, a page of results)."""
        if not self._sigs:
            return ToolLoopVerdict("ok")
        last, gist = self._sigs[-1], self._gist[-1]
        matches = [s == last and g == gist for s, g in zip(self._sigs, self._gist, strict=True)]
        count = sum(matches)
        if count >= self.repeat_break:
            if self._never_ran(self._same_call()):
                return ToolLoopVerdict(
                    "break", f"{self._names[-1]} was refused or failed {count}× — nothing ran"
                )
            return ToolLoopVerdict("break", f"{self._names[-1]} called with identical args {count}×")
        if count >= self.repeat_warn:
            return ToolLoopVerdict("warn", f"{self._names[-1]} repeated {count}× with identical args")
        return ToolLoopVerdict("ok")

    def _no_progress(self) -> ToolLoopVerdict:
        """Same tool + same observation, back to back — a poll that never changes.

        With the same args, with args that differ only in shape (``_target_sig``: depth, a limit, a
        spelling of the same path), or with calls that all failed — refused, errored, or answering a
        failure in their output (``_answered_failure``). Four DIFFERENT calls that succeeded
        with the same confirmation (four edits, each ``replaced 1 occurrence``) are four pieces of
        work. The observation is compared exactly, not as a gist: pages of a log that differ only in
        their timestamps are new pages.
        """
        if len(self._obs) < self.stall_break or not self._obs[-1]:
            return ToolLoopVerdict("ok")
        name, obs, sig, target = self._names[-1], self._obs[-1], self._sigs[-1], self._targets[-1]
        run = 0
        for n, o, s, t, failed in zip(
            reversed(self._names), reversed(self._obs), reversed(self._sigs), reversed(self._targets),
            reversed(self._failed), strict=True,
        ):
            if n == name and o == obs and (s == sig or t == target or failed):
                run += 1
            else:
                break
        if run >= self.stall_break:
            tail = [i >= len(self._names) - run for i in range(len(self._names))]
            # Wall or loop is asked of the CALL, not of the answer: if these same args got through
            # once in the window, something ran, whatever the recent repeats returned.
            same = self._same_call()
            if self._never_ran([t or s for t, s in zip(tail, same, strict=True)]):
                return ToolLoopVerdict(
                    "break", f"{name} was refused or failed {run}× — nothing ran"
                )
            answered = [f and ok is not False for f, ok in zip(self._failed, self._ok, strict=True)]
            if all(a for a, t in zip(answered, tail, strict=True) if t):
                # It ran, and it failed the same way each time, in the tool's own words — not a
                # poll. A run the loop itself saw fail keeps the wall/loop wording above.
                return ToolLoopVerdict("break", f"{name} failed the same way {run}×")
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
