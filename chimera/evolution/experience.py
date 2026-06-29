"""Experience buffer — the seed of the self-evolution engine.

Records each autonomous attempt and its outcome. Failures become negative examples
and successes become positive ones (per HORIZON), so future runs can learn from past
attempts. v1 is a simple JSON-backed log; the evolution engine (M4) builds on it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Outcome = Literal["success", "failure"]


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

    def __len__(self) -> int:
        return len(self._items)
