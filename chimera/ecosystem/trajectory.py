"""Trajectory collection — the seed of opt-in model-level evolution.

Records (prompt, response, outcome) trajectories from agent runs and exports them as
SFT or DPO datasets. Actual fine-tuning (LoRA/SFT/DPO) is intentionally **opt-in and
external** — Chimera collects the data and gets out of the way; nothing here trains a
model or changes weights automatically.

Two things this file used to get wrong, both of the family where **data disappears and nothing
complains**. ``load()`` validated every line with no guard, so one truncated or schema-drifted
record — a kill mid-append, a field added in a later version — raised out of the constructor and
took every ``chimera evolve`` command with it, including the ones whose whole job is to tell you
what the history holds. And the log grew without a ceiling in either direction: unbounded on disk,
and fully resident in memory on open.

``chimera/memory/store.py`` had already learned the first half and written the reason down. The
lesson had simply never crossed to the file next door.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from chimera.core.filelock import read_text
from chimera.telemetry import get_logger

_log = get_logger("ecosystem.trajectory")

Outcome = Literal["success", "failure", "unknown"]

#: Rotate the log once it passes this, keeping exactly one previous generation. Same policy and
#: same reasoning as the step trace: rename rather than trim, because rewriting to drop old lines
#: has to read all of it and a crash mid-rewrite loses the whole history instead of its oldest part.
MAX_TRAJECTORY_BYTES = 64 * 1024 * 1024

#: How many trajectories stay resident. Curation and export read the tail — the recent runs are the
#: ones that describe the agent as it is now — so an older prefix on disk is not lost, just not
#: carried in memory. Both numbers are here rather than inline so a deployment can say what it wants.
MAX_RESIDENT = 50_000


class Trajectory(BaseModel):
    seq: int
    prompt: str
    response: str
    outcome: Outcome = "unknown"
    reward: float = 0.0
    steps: int = 0  # tool-calling steps — a long-horizon signal for data curation
    events: list[dict[str, Any]] = Field(default_factory=list)  # per-tool-step {tool, ok}
    # Diff-gate (nanobot "Dream"): did the run actually change the workspace? None = untracked
    # (no guard/workspace), True/False = certified by the real snapshot diff. ``diff_summary`` is
    # machine-derived (files +/-), so it — not the model's narrative — is the honest audit record.
    diff_productive: bool | None = None
    diff_summary: str | None = None

    def process_score(self) -> float:
        """Step-following process signal in [0, 1] — the SkillCoach data-quality filter."""
        from chimera.ecosystem.events import step_following_score

        return step_following_score(self.events)


def _rotate_if_large(path: Path) -> None:
    """Move the log aside once it passes the cap, keeping exactly one previous generation.

    Rename rather than trim, for the reason the step trace gives: rewriting the file to drop old
    lines has to read all of it, and a crash mid-rewrite loses the whole history instead of the
    oldest part of it. A rotation that fails must not take the run down — the trajectory is evidence
    about the work, not the work.
    """
    try:
        if path.exists() and path.stat().st_size >= MAX_TRAJECTORY_BYTES:
            path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError as exc:  # pragma: no cover - disk-shaped failure
        _log.warning("could not rotate %s: %s", path.name, exc)


class TrajectoryCollector:
    """A JSONL-backed append-only log of trajectories, exportable for training."""

    def __init__(self, path: Path, *, max_resident: int = MAX_RESIDENT) -> None:
        self.path = Path(path)
        self.max_resident = max_resident
        self._items: list[Trajectory] = []
        #: Next sequence number to hand out. Tracked separately from ``len(self._items)`` because
        #: the resident window can be smaller than the history: numbering from the length would
        #: restart mid-file and hand two different runs the same seq.
        self._next_seq = 0
        self.skipped = 0
        self.load()

    def load(self) -> None:
        """Read the log, skipping any line that will not parse.

        A malformed record is a thing that happens — a process killed between the write and the
        newline, a field that changed shape across versions, a hand edit. Letting one of those abort
        the load makes the entire history unreadable, which is a strictly worse outcome than losing
        the one line, and it fails in the commands you would reach for to diagnose it.

        The count of skips is kept on ``self.skipped`` rather than only logged, so a caller can say
        "997 of 1000" instead of quietly reporting 997 as if that were all there ever was.
        """
        self._items = []
        self.skipped = 0
        highest = -1
        if not self.path.exists():
            self._next_seq = 0
            return
        for number, line in enumerate(read_text(self.path).splitlines(), start=1):
            if not line.strip():
                continue
            try:
                item = Trajectory.model_validate_json(line)
            except ValidationError as exc:
                self.skipped += 1
                _log.warning("skipping malformed trajectory at line %d: %s", number, exc)
                continue
            highest = max(highest, item.seq)
            self._items.append(item)
        # Keep only the tail in memory. The prefix stays on disk and is still there for anything
        # that wants to read the file directly; what it is not is resident in a long-lived process.
        if len(self._items) > self.max_resident:
            _log.info(
                "trajectory log holds %d records; keeping the most recent %d in memory",
                len(self._items), self.max_resident,
            )
            self._items = self._items[-self.max_resident :]
        self._next_seq = highest + 1

    def record(
        self,
        prompt: str,
        response: str,
        *,
        outcome: Outcome = "unknown",
        reward: float = 0.0,
        steps: int = 0,
        events: list[dict[str, Any]] | None = None,
        diff_productive: bool | None = None,
        diff_summary: str | None = None,
    ) -> Trajectory:
        item = Trajectory(
            seq=self._next_seq,
            prompt=prompt,
            response=response,
            outcome=outcome,
            reward=reward,
            steps=steps,
            events=list(events or []),
            diff_productive=diff_productive,
            diff_summary=diff_summary,
        )
        self._next_seq += 1
        self._items.append(item)
        if len(self._items) > self.max_resident:
            del self._items[0]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_large(self.path)
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
