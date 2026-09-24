"""Read M6's oracle scores, receipts and breaker/escalation events into PREREGISTRATION's outcomes.

Run in WSL:  ~/hb-venv-m6/bin/python read_m6.py [--json out.json]

Per executor: the paired esc - stop difference over the 6 tasks (each task = mean over its replicas),
with a bootstrap CI over TASKS, beside the stop arm's own replica-to-replica SD (read_b4b's floor, the
same computation). Frozen cells are MISSING (B4 amendment 4). Nothing here spends.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
from pathlib import Path
from typing import Any

HB = Path(os.path.expanduser("~/harness-bench"))
HOMES = Path(os.path.expanduser("~/hb-homes"))
FROZEN = Path(os.path.expanduser("~/hb-frozen-m6.txt"))
TASKS = [
    "041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety",
    "082-compose-config-repair", "086-sql-migration-preflight-rollback", "087-cli-parser-bug-tests",
]
EXECUTORS = ("strong", "weak")
TARGET_TAIL = {"strong": "gpt-6-sol", "weak": "deepseek-v3.2"}
REPLICAS = (0, 1, 2)
SEED, DRAWS = 20260924, 10000


def outcome_of(hid: str, task: str) -> float | None:
    hits = glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    v = (json.loads(Path(hits[0]).read_text(encoding="utf-8")).get("oracle_result") or {}).get("outcome_score")
    return float(v) if isinstance(v, int | float) else None


def usd_of(hid: str, task: str) -> float | None:
    p = HOMES / f"{task}-{hid}" / "runs.jsonl"
    if not p.is_file():
        return None
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows or any(r.get("usd") is None for r in rows):
        return None
    return sum(float(r["usd"]) for r in rows)


def events_of(hid: str, task: str, target_tail: str) -> tuple[bool, bool]:
    """(breaker ended the run, the run escalated) from the step log."""
    p = HOMES / f"{task}-{hid}" / "traces.jsonl"
    if not p.is_file():
        return False, False
    looped = escalated = False
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        looped = looped or row.get("stopped_reason") == "tool_loop"
        escalated = escalated or any(target_tail in str(s.get("model") or "") for s in row.get("steps") or [])
    return looped, escalated


def frozen() -> set[tuple[str, str]]:
    if not FROZEN.is_file():
        return set()
    out: set[tuple[str, str]] = set()
    for line in FROZEN.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out.add((parts[0], parts[1]))
    return out


def boot(xs: list[float]) -> tuple[float, float]:
    rng = random.Random(SEED)
    draws = sorted(statistics.mean(rng.choices(xs, k=len(xs))) for _ in range(DRAWS))
    return draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    cold = frozen()
    summary: dict[str, Any] = {"frozen": sorted(" ".join(c) for c in cold), "executors": {}}
    for ex in EXECUTORS:
        cells: dict[tuple[str, str, int], dict[str, Any]] = {}
        for arm in ("stop", "esc"):
            for k in REPLICAS:
                hid = f"m6-{arm}-{ex}-r{k}"
                for task in TASKS:
                    missing = (task, hid) in cold
                    looped, escalated = events_of(hid, task, TARGET_TAIL[ex])
                    cells[(task, arm, k)] = {"outcome": None if missing else outcome_of(hid, task),
                                             "usd": None if missing else usd_of(hid, task),
                                             "looped": looped, "escalated": escalated}

        def mean_of(task: str, arm: str, field: str, cells: dict[tuple[str, str, int], dict[str, Any]] = cells) -> float | None:
            vals = [cells[(task, arm, k)][field] for k in REPLICAS if cells[(task, arm, k)][field] is not None]
            return statistics.mean(vals) if vals else None

        sds = [statistics.stdev(v) for task in TASKS
               if len(v := [cells[(task, "stop", k)]["outcome"] for k in REPLICAS if cells[(task, "stop", k)]["outcome"] is not None]) >= 2]
        floor = statistics.mean(sds) if sds else float("nan")
        diffs, stop_means, esc_means = [], [], []
        for task in TASKS:
            s, e = mean_of(task, "stop", "outcome"), mean_of(task, "esc", "outcome")
            if s is not None and e is not None:
                diffs.append(e - s)
                stop_means.append(s)
                esc_means.append(e)
        lo, hi = boot(diffs) if len(diffs) >= 2 else (float("nan"), float("nan"))
        n_cells = {arm: sum(1 for (t, a, k), c in cells.items() if a == arm and c["outcome"] is not None) for arm in ("stop", "esc")}
        looped = {arm: sum(1 for (t, a, k), c in cells.items() if a == arm and c["looped"]) for arm in ("stop", "esc")}
        escalated = [c for (t, a, k), c in cells.items() if a == "esc" and c["escalated"]]
        esc_scores = [c["outcome"] for c in escalated if c["outcome"] is not None]
        usd = {arm: [c["usd"] for (t, a, k), c in cells.items() if a == arm and c["usd"] is not None] for arm in ("stop", "esc")}
        p08 = {arm: sum(1 for (t, a, k), c in cells.items() if a == arm and (c["outcome"] or 0) >= 0.8) / max(1, n_cells[arm]) for arm in ("stop", "esc")}
        print(f"\n===== executor: {ex} =====")
        print(f"  scored cells: stop {n_cells['stop']}/18  esc {n_cells['esc']}/18  · floor (stop replica SD) {floor:.4f}")
        print(f"  outcome  stop {statistics.mean(stop_means):.4f}  esc {statistics.mean(esc_means):.4f}  "
              f"Δ {statistics.mean(diffs):+.4f}  95% CI over tasks [{lo:+.4f}, {hi:+.4f}]")
        print(f"  pass@0.8 stop {p08['stop']:.3f}  esc {p08['esc']:.3f}")
        print(f"  breaker ended the run: stop {looped['stop']}/18  esc {looped['esc']}/18 · escalated in esc: {len(escalated)}/18"
              f" (their mean score {statistics.mean(esc_scores) if esc_scores else float('nan'):.3f})")
        print(f"  usd/solve  stop {statistics.mean(usd['stop']) if usd['stop'] else float('nan'):.4f}  "
              f"esc {statistics.mean(usd['esc']) if usd['esc'] else float('nan'):.4f}  (n {len(usd['stop'])}/{len(usd['esc'])})")
        summary["executors"][ex] = {
            "floor": floor, "delta": statistics.mean(diffs), "ci95": [lo, hi], "per_task_delta": dict(zip(TASKS, diffs, strict=False)),
            "stop_mean": statistics.mean(stop_means), "esc_mean": statistics.mean(esc_means), "pass08": p08,
            "breaker_ended": looped, "escalated": len(escalated), "escalated_mean_score": statistics.mean(esc_scores) if esc_scores else None,
            "usd_per_solve": {a: (statistics.mean(v) if v else None) for a, v in usd.items()}, "scored": n_cells,
        }
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
