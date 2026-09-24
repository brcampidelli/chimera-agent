"""Per-cell M6 table and the reading conditioned on the breaker. Read-only; run in WSL after read_m6.py.

    ~/hb-venv-m6/bin/python cells_m6.py [--json results/cells.json]

Before the breaker trips the two arms are the same configuration, so which runs trip is exchangeable
between them. That makes two readings possible that the arm-level Δ hides:

* **clean runs** (the breaker never tripped): identical config in both arms, so their difference is
  a negative control for the apparatus;
* **tripped runs**: in `stop` the run ended there, in `esc` it was handed to the stronger model. The
  difference is the effect of escalating on the runs it acts on — tiny n, and NOT a registered test.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

from read_m6 import EXECUTORS, HOMES, REPLICAS, TARGET_TAIL, TASKS, events_of, outcome_of, usd_of


def steps_of(hid: str, task: str, target_tail: str) -> tuple[int, int]:
    """(steps in the trace, steps answered by the escalation target)."""
    steps = on_target = 0
    p = HOMES / f"{task}-{hid}" / "traces.jsonl"
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        for s in row.get("steps") or []:
            steps += 1
            on_target += target_tail in str(s.get("model") or "")
    return steps, on_target


def cells() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ex in EXECUTORS:
        for arm in ("stop", "esc"):
            for k in REPLICAS:
                hid = f"m6-{arm}-{ex}-r{k}"
                for task in TASKS:
                    looped, escalated = events_of(hid, task, TARGET_TAIL[ex])
                    steps, on_target = steps_of(hid, task, TARGET_TAIL[ex])
                    out.append({"executor": ex, "arm": arm, "replica": k, "task": task,
                                "outcome": outcome_of(hid, task), "usd": usd_of(hid, task),
                                "tripped": looped or escalated, "escalated": escalated,
                                "steps": steps, "steps_on_target": on_target})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows = cells()
    for ex in EXECUTORS:
        print(f"== {ex}")
        for arm in ("stop", "esc"):
            for tripped in (False, True):
                g = [r for r in rows if r["executor"] == ex and r["arm"] == arm and r["tripped"] is tripped]
                if not g:
                    print(f"  {arm:4s} {'tripped' if tripped else 'clean':7s} n= 0")
                    continue
                print(f"  {arm:4s} {'tripped' if tripped else 'clean':7s} n={len(g):2d}  "
                      f"score {statistics.mean(r['outcome'] or 0.0 for r in g):.3f}  "
                      f"usd {statistics.mean(r['usd'] or 0.0 for r in g):.4f}  "
                      f"steps {statistics.mean(r['steps'] for r in g):.1f}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
