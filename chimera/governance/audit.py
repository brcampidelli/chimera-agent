"""Append-only audit log (JSONL) for governance decisions and evolution changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AuditLog:
    """A simple append-only JSONL audit trail."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._count = self._initial_count()

    def _initial_count(self) -> int:
        if not self.path.exists():
            return 0
        return sum(1 for _ in self.path.read_text(encoding="utf-8").splitlines() if _.strip())

    def record(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        entry = {"seq": self._count, "type": event_type, **payload}
        self._count += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return entry

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        return [
            json.loads(line)
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def __len__(self) -> int:
        return self._count
