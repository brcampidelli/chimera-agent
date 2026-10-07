"""Create a deterministic, self-contained synthetic history.db for S30-55."""

from __future__ import annotations

import argparse
import random
from pathlib import Path

from bench.memory_selection.items import ITEMS
from chimera.memory.history import HistoryIndex


def generate(path: Path, *, seed: int = 3055) -> None:
    """Write exactly 30 relevant and 870 distractor turns in a fixed candidate pool."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    rng = random.Random(seed)
    index = HistoryIndex(path.parent)
    try:
        # Project scoping gives each query the same fixed 30-candidate pool; only one candidate
        # contains its registered answer.
        for item in ITEMS:
            project = f"synthetic-memory-selection/{item.id}"
            for ordinal in range(30):
                turn_id = item.relevant_turn if ordinal == 0 else f"{item.id}-noise-{ordinal:02d}"
                distractor = ordinal != 0
                words = [item.query.split()[-1], "discussion", "followup"]
                rng.shuffle(words)
                asked = f"Review archive {item.query.split()[-1]}: {' '.join(words)}"
                answered = (
                    item.answer
                    if not distractor
                    else f"No final decision recorded; pending archive follow-up {ordinal}."
                )
                index.record(
                    turn_id=turn_id,
                    session_id=f"session-{item.id}",
                    project=project,
                    asked=asked,
                    answered=answered,
                    asked_at=float(1_800_000_000 + ordinal),
                )
    finally:
        index.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=3055)
    args = parser.parse_args()
    generate(args.out, seed=args.seed)
    print(f"wrote {len(ITEMS) * 30} turns to {args.out}")


if __name__ == "__main__":
    main()
