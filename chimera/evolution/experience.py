"""Experience buffer — the seed of the self-evolution engine.

Records each autonomous attempt and its outcome. Failures become negative examples
and successes become positive ones (per HORIZON), so future runs can learn from past
attempts. v1 is a simple JSON-backed log; the evolution engine (M4) builds on it.

This one is written from ``AutonomousLoop`` after every attempt, which puts it on the 24/7 path,
and its ``save()`` used to be a bare ``write_text``. That truncates before it fills, so a kill or a
full disk during the write leaves a file that is neither the old contents nor the new — and because
the whole buffer is one JSON array, the loss is **every** lesson ever recorded, not the last one.
An unattended agent restarted at the wrong moment would come back with no history and no error to
explain where it went.

Three of the four fixes here are the same ones :mod:`chimera.memory.store` already carried, which
is the actual lesson: whole-file JSON stores share a failure family, so the primitives now live in
one place (:mod:`chimera.core.filelock`) instead of being rediscovered per store. The fourth is
this file's own — ``load()`` parsed the array in one call, so one bad record made the whole buffer
unreadable, and the ``all()`` in ``chimera evolve`` is where you would find out.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from chimera.core.filelock import atomic_write_text, exclusively, read_text
from chimera.telemetry import get_logger

_log = get_logger("evolution.experience")

Outcome = Literal["success", "failure"]

_WORD = re.compile(r"[a-z0-9]+")

#: How many attempts stay in the buffer. ``relevant()`` scores every entry on every planning step,
#: so this is a latency ceiling as much as a memory one, and the oldest attempts are the least
#: likely to describe the agent as it is now. Dropping the head is deliberate and logged.
MAX_EXPERIENCES = 20_000


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if len(word) >= 2}


class Experience(BaseModel):
    """One recorded attempt."""

    seq: int
    task: str
    outcome: Outcome
    detail: str = ""


class ExperienceBuffer:
    """A JSON-backed, append-only log of attempts."""

    def __init__(self, path: Path, *, max_items: int = MAX_EXPERIENCES) -> None:
        self.path = Path(path)
        self.max_items = max_items
        self._items: list[Experience] = []
        #: Next sequence number. Tracked rather than derived from ``len(self._items)`` because the
        #: buffer is capped: numbering from the length would restart once the head is dropped and
        #: hand two different attempts the same seq, which ``relevant()`` uses as its tiebreak.
        self._next_seq = 0
        self._lock = threading.RLock()
        self.skipped = 0
        self.load()

    def load(self) -> None:
        """Read the buffer, skipping any record that will not validate.

        Per record, not per file. Validating the array in one call means a single entry with a
        field from another version makes every other lesson unreadable — and the commands you would
        reach for to look at the history are the ones that fail.

        A file that is not JSON at all is a different case and is reported as an empty buffer with a
        warning rather than an exception, because the alternative is an autonomous loop that will
        not start.
        """
        self._items = []
        self.skipped = 0
        raw_text = read_text(self.path)
        if not raw_text.strip():
            self._next_seq = 0
            return
        try:
            raw: Any = json.loads(raw_text)
        except ValueError as exc:
            _log.warning("experience buffer at %s is not readable JSON: %s", self.path, exc)
            self._next_seq = 0
            return
        if not isinstance(raw, list):
            _log.warning("experience buffer at %s is not a list; ignoring", self.path)
            self._next_seq = 0
            return
        for entry in raw:
            try:
                self._items.append(Experience.model_validate(entry))
            except ValidationError as exc:
                self.skipped += 1
                _log.warning("skipping malformed experience record: %s", exc)
        if self.skipped:
            _log.warning("%d experience record(s) skipped as malformed", self.skipped)
        self._next_seq = max((item.seq for item in self._items), default=-1) + 1
        self._trim()

    def _trim(self) -> None:
        if len(self._items) > self.max_items:
            dropped = len(self._items) - self.max_items
            _log.info("experience buffer over %d; dropping %d oldest", self.max_items, dropped)
            del self._items[:dropped]

    def _write(self) -> None:
        """Serialise to disk atomically. Assumes the caller holds the locks."""
        atomic_write_text(self.path, json.dumps([i.model_dump() for i in self._items], indent=2))

    def save(self) -> None:
        """Write the buffer out atomically, under the lock.

        The blunt form, kept for callers holding the whole picture: it does **not** merge concurrent
        writes. :meth:`record` does.
        """
        with self._lock, exclusively(self.path):
            self._write()

    def record(self, task: str, outcome: Outcome, detail: str = "") -> Experience:
        """Append one attempt, re-reading first so a second writer's lessons are not erased.

        The reload matters here for the same reason it does in the memory store: ``chimera serve``
        runs the autonomous work in one process and an operator can run ``chimera`` in another, and
        without it whichever writes second republishes a snapshot from before the other started.
        """
        with self._lock, exclusively(self.path):
            self.load()
            exp = Experience(seq=self._next_seq, task=task, outcome=outcome, detail=detail)
            self._next_seq += 1
            self._items.append(exp)
            self._trim()
            self._write()
        return exp

    def all(self) -> list[Experience]:
        return list(self._items)

    def failures(self) -> list[Experience]:
        return [e for e in self._items if e.outcome == "failure"]

    def successes(self) -> list[Experience]:
        return [e for e in self._items if e.outcome == "success"]

    def relevant(self, task: str, k: int = 3) -> list[Experience]:
        """Prior experiences most relevant to ``task``, by task-token overlap.

        Failures are favoured slightly — a negative example ("this approach failed")
        carries more planning signal than another success. Returns at most ``k``,
        most-relevant first; empty when nothing overlaps.
        """
        query = _tokens(task)
        if not query:
            return []
        scored: list[tuple[float, int, Experience]] = []
        for exp in self._items:
            overlap = len(query & _tokens(exp.task))
            if overlap == 0:
                continue
            score = overlap + (0.5 if exp.outcome == "failure" else 0.0)
            # newer entries win ties (higher seq), so -seq sorts ascending as a tiebreak
            scored.append((score, exp.seq, exp))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [exp for _, _, exp in scored[:k]]

    def __len__(self) -> int:
        return len(self._items)


def format_lessons(items: list[Experience]) -> str:
    """Render prior experiences as a compact 'lessons' block for a planner prompt.

    Returns "" for an empty list so callers can fold it into context unconditionally.
    The lessons are advisory only: the attempt they shape is still gated by
    verify-or-revert, so a misleading lesson can never corrupt the workspace.
    """
    if not items:
        return ""
    lines = ["Lessons from past attempts on similar tasks (avoid repeating failures):"]
    for exp in items:
        tag = "FAILED" if exp.outcome == "failure" else "ok"
        detail = f" — {exp.detail}" if exp.detail else ""
        lines.append(f"- [{tag}] {exp.task}{detail}")
    return "\n".join(lines)
