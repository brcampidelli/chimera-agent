"""Read the M6-fork pairs into PREREGISTRATION's outcomes. Nothing here spends or runs an oracle.

  ~/hb-venv-m6f/bin/python read_m6f.py [--pairs results/pairs.json] [--json results/summary.json]

Refuses to read while any cell fails the copy check (grade_fork.py): the stop arm is a graded COPY, so
a copy that grades differently from the harness would make every pair a comparison of two rulers.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
from pathlib import Path
from typing import Any

HOMES = Path(os.path.expanduser("~/hb-homes"))
SEED, DRAWS = 20260925, 10000


def usd_of(hid: str, task: str) -> float | None:
    p = HOMES / f"{task}-{hid}" / "runs.jsonl"
    if not p.is_file():
        return None
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows or any(r.get("usd") is None for r in rows):
        return None
    return sum(float(r["usd"]) for r in rows)


def boot(xs: list[float]) -> tuple[float, float]:
    rng = random.Random(SEED)
    draws = sorted(statistics.mean(rng.choices(xs, k=len(xs))) for _ in range(DRAWS))
    return draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=Path, default=Path("results/pairs.json"))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    rows: list[dict[str, Any]] = json.loads(args.pairs.read_text(encoding="utf-8"))
    bad = [r for r in rows if not r["copy_ok"]]
    if bad:
        raise SystemExit(f"copy check failed on {len(bad)} cell(s) — the two arms are not on one ruler: {bad[:3]}")
    summary: dict[str, Any] = {"executors": {}}
    for ex in ("strong", "weak"):
        cells = [r for r in rows if r["executor"] == ex]
        pairs = [r for r in cells if r["tripped"] and r["stop"] is not None and r["esc"] is not None]
        if not cells:
            continue
        diffs = [r["esc"] - r["stop"] for r in pairs]
        lo, hi = boot(diffs) if len(diffs) >= 2 else (float("nan"), float("nan"))
        rate = len(pairs) / len(cells)
        usd_trip = [u for r in pairs if (u := usd_of(r["hid"], r["task"])) is not None]
        usd_clean = [u for r in cells if not r["tripped"] and (u := usd_of(r["hid"], r["task"])) is not None]
        by_task = {t: [r["esc"] - r["stop"] for r in pairs if r["task"] == t] for t in sorted({r["task"] for r in pairs})}
        mean = statistics.mean(diffs) if diffs else float("nan")
        print(f"\n===== executor: {ex} =====")
        print(f"  solves {len(cells)} · pairs (tripped) {len(pairs)} · trip rate {rate:.3f}")
        if diffs:
            print(f"  stop {statistics.mean(r['stop'] for r in pairs):.4f}  esc {statistics.mean(r['esc'] for r in pairs):.4f}  "
                  f"Δ {mean:+.4f}  95% CI over pairs [{lo:+.4f}, {hi:+.4f}]")
            print(f"  pairs where escalation helped / hurt / tied: {sum(d > 0.001 for d in diffs)} / "
                  f"{sum(d < -0.001 for d in diffs)} / {sum(abs(d) <= 0.001 for d in diffs)}")
            for t, v in by_task.items():
                print(f"    {t:<26} n={len(v):2d}  Δ {statistics.mean(v):+.3f}")
            print(f"  implied arm-level effect (trip rate × Δ): {rate * mean:+.4f}")
        print(f"  US$/solve: tripped {statistics.mean(usd_trip) if usd_trip else float('nan'):.4f} (n {len(usd_trip)}) · "
              f"clean {statistics.mean(usd_clean) if usd_clean else float('nan'):.4f} (n {len(usd_clean)})")
        summary["executors"][ex] = {
            "solves": len(cells), "pairs": len(pairs), "trip_rate": rate, "delta": mean, "ci95": [lo, hi],
            "helped_hurt_tied": [sum(d > 0.001 for d in diffs), sum(d < -0.001 for d in diffs), sum(abs(d) <= 0.001 for d in diffs)],
            "by_task": {t: {"n": len(v), "delta": statistics.mean(v)} for t, v in by_task.items()},
            "usd_per_solve": {"tripped": statistics.mean(usd_trip) if usd_trip else None,
                              "clean": statistics.mean(usd_clean) if usd_clean else None},
        }
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
