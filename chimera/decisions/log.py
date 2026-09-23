"""The decision log — every answer with its receipt, and a later line that says what was true.

``<home>/decisions/decisions.jsonl``, append-only. Two kinds of line:

* ``{"kind": "answer", "id": …, …receipt…}`` — written by the :class:`~chimera.decisions.contract.Decider`
  the moment it answers. It carries ``raw_p``, the number **before** the map, because that is what a
  refit is fitted on: the calibrated ``p`` the pending-approval history keeps is the output of the
  map being refitted, and a map fitted on its own output is a map fitted on nothing (§2z).
* ``{"kind": "outcome", "id": …, "label": 0|1, "source": …}`` — written later, by whoever learned
  what was true: the person who answered the card's second question (*was this dangerous?*), the
  ``chimera decisions label`` command, a verifier or an oracle on a surface that has one. The label
  is 1 for the question's event.

Nothing is rewritten: an answer line is final when written, and a later outcome for the same id
supersedes an earlier one on read. A label is never inferred from the approval itself — a person
approves a dangerous action they meant to run, and refuses a harmless one they did not expect; the
approve button answers "may it run?", not "was it dangerous?" (study 22, phase 2).

The state is kept truncated (``STATE_CHARS``) for a reader who checks a label, and hashed in full so
two answers about one text can be told apart from two answers about two texts that share a prefix.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

FILE = "decisions.jsonl"
STATE_CHARS = 500
SOURCES = ("card", "cli", "verifier", "oracle", "bench")


def log_path(home: Path) -> Path:
    return Path(home) / "decisions" / FILE


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()[:16]


class DecisionLog:
    """Appends answers and outcomes to one file. A failure to write never fails the decision."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    @classmethod
    def for_home(cls, home: Path) -> DecisionLog:
        return cls(log_path(home))

    def _append(self, line: dict[str, Any]) -> bool:
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(line, ensure_ascii=False) + "\n")
            return True
        except OSError as exc:
            _log.warning("could not write to the decision log: %s", exc)
            return False

    def answer(self, receipt: dict[str, Any], state: str, *, raw_p: float | None) -> str:
        """Record one answer; return its id (``""`` when the line could not be written)."""
        entry_id = uuid.uuid4().hex[:12]
        line: dict[str, Any] = {
            "kind": "answer", "id": entry_id, "at": round(time.time(), 3), **receipt,
            "state": state[:STATE_CHARS], "state_hash": state_hash(state),
        }
        # The receipt rounds `raw_p` to four places and only when calibrated; the refit needs it
        # always, and UNROUNDED — six places moved the refitted slope in the sixth decimal, and a refit
        # on logged rows must equal a refit on the rows themselves.
        if raw_p is not None:
            line["raw_p"] = float(raw_p)
        return entry_id if self._append(line) else ""

    def outcome(self, entry_id: str, label: bool, *, source: str, note: str = "") -> bool:
        if source not in SOURCES:
            raise ValueError(f"unknown outcome source {source!r}; one of {', '.join(SOURCES)}")
        if not entry_id.strip():
            raise ValueError("an outcome needs the id of the answer it labels")
        line: dict[str, Any] = {
            "kind": "outcome", "id": entry_id, "at": round(time.time(), 3), "label": 1 if label else 0,
            "source": source,
        }
        if note:
            line["note"] = note[:300]
        return self._append(line)


@dataclass(frozen=True)
class Row:
    """One answer joined to its latest outcome, if any."""

    answer: dict[str, Any]
    label: int | None
    source: str | None

    @property
    def id(self) -> str:
        return str(self.answer.get("id", ""))

    @property
    def raw_p(self) -> float | None:
        value = self.answer.get("raw_p")
        return float(value) if isinstance(value, (int, float)) else None


def read(path: Path) -> list[Row]:
    """Every answer, oldest first, with its latest outcome. Unreadable lines are skipped; an outcome
    whose answer is not in the file is ignored (it labels nothing this reader can see)."""
    if not Path(path).exists():
        return []
    answers: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    outcomes: dict[str, tuple[int, str]] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(line, dict):
            continue
        entry_id = str(line.get("id") or "")
        if not entry_id:
            continue
        if line.get("kind") == "answer":
            if entry_id not in answers:
                order.append(entry_id)
            answers[entry_id] = line
        elif line.get("kind") == "outcome" and line.get("label") in (0, 1):
            outcomes[entry_id] = (int(line["label"]), str(line.get("source") or ""))
    rows: list[Row] = []
    for entry_id in order:
        label, source = outcomes.get(entry_id, (None, None))
        rows.append(Row(answer=answers[entry_id], label=label, source=source))
    return rows


def find(path: Path, entry_id: str) -> Row | None:
    for row in read(path):
        if row.id == entry_id:
            return row
    return None
