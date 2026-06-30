"""Experience buffer — the seed of the self-evolution engine.

Records each autonomous attempt and its outcome. Failures become negative examples
and successes become positive ones (per HORIZON), so future runs can learn from past
attempts. v1 is a simple JSON-backed log; the evolution engine (M4) builds on it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Outcome = Literal["success", "failure"]

_WORD = re.compile(r"[a-z0-9]+")


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

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._items: list[Experience] = []
        self.load()

    def load(self) -> None:
        self._items = []
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        self._items = [Experience.model_validate(item) for item in raw]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [item.model_dump() for item in self._items]
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def record(self, task: str, outcome: Outcome, detail: str = "") -> Experience:
        exp = Experience(seq=len(self._items), task=task, outcome=outcome, detail=detail)
        self._items.append(exp)
        self.save()
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
