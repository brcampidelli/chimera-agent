"""Trajectory collection — the seed of opt-in model-level evolution.

Records (prompt, response, outcome) trajectories from agent runs and exports them as
SFT or DPO datasets. Actual fine-tuning (LoRA/SFT/DPO) is intentionally **opt-in and
external** — Chimera collects the data and gets out of the way; nothing here trains a
model or changes weights automatically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

Outcome = Literal["success", "failure", "unknown"]


class Trajectory(BaseModel):
    seq: int
    prompt: str
    response: str
    outcome: Outcome = "unknown"
    reward: float = 0.0


class TrajectoryCollector:
    """A JSONL-backed append-only log of trajectories, exportable for training."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._items: list[Trajectory] = []
        self.load()

    def load(self) -> None:
        self._items = []
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                self._items.append(Trajectory.model_validate_json(line))

    def record(
        self, prompt: str, response: str, *, outcome: Outcome = "unknown", reward: float = 0.0
    ) -> Trajectory:
        item = Trajectory(seq=len(self._items), prompt=prompt, response=response, outcome=outcome, reward=reward)
        self._items.append(item)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(item.model_dump_json() + "\n")
        return item

    def all(self) -> list[Trajectory]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def export_sft(self, out_path: Path) -> int:
        """Write successful trajectories as chat-format SFT examples. Returns count."""
        rows = [
            {
                "messages": [
                    {"role": "user", "content": item.prompt},
                    {"role": "assistant", "content": item.response},
                ]
            }
            for item in self._items
            if item.outcome == "success"
        ]
        _write_jsonl(out_path, rows)
        return len(rows)

    def export_dpo(self, out_path: Path) -> int:
        """Write preference pairs (chosen=success, rejected=failure) per prompt."""
        by_prompt: dict[str, dict[str, str]] = {}
        for item in self._items:
            slot = by_prompt.setdefault(item.prompt, {})
            if item.outcome == "success":
                slot.setdefault("chosen", item.response)
            elif item.outcome == "failure":
                slot.setdefault("rejected", item.response)
        rows = [
            {"prompt": prompt, "chosen": slot["chosen"], "rejected": slot["rejected"]}
            for prompt, slot in by_prompt.items()
            if "chosen" in slot and "rejected" in slot
        ]
        _write_jsonl(out_path, rows)
        return len(rows)


def _write_jsonl(out_path: Path, rows: list[dict[str, Any]]) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row) for row in rows) + ("\n" if rows else ""), encoding="utf-8")
