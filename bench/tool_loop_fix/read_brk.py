"""Read the breaker re-measurement into PREREGISTRATION's outcomes. Nothing here spends.

Run in WSL:  ~/hb-venv-brk-fixed/bin/python read_brk.py [--json results/summary.json]

Per executor: the fixed-minus-legacy difference in mean oracle score, each task weighted equally, with a
bootstrap CI that resamples solves WITHIN each (task, arm) cell — the arms are independent runs, not
pairs — beside the legacy arm's own replica SD. Frozen cells are missing.
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
FROZEN = Path(os.path.expanduser("~/hb-frozen-brk.txt"))
TASKS = ["041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety", "082-compose-config-repair"]
ARMS = ("legacy", "fixed")
REPLICAS = range(10)
SEED, DRAWS = 20260926, 10000


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


def stop_of(hid: str, task: str) -> tuple[bool, str, int]:
    """(the breaker ended the run, the pattern of its last four tool calls, steps).

    The stop path logs its reason at DEBUG, so it is not in the solve log; the pattern is rebuilt
    from the trace instead: one tool or several, args all the same or distinct, output the same or not.
    """
    p = HOMES / f"{task}-{hid}" / "traces.jsonl"
    looped, steps, calls = False, 0, []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            looped = looped or row.get("stopped_reason") == "tool_loop"
            for st in row.get("steps") or []:
                steps += 1
                calls.extend((t.get("name"), str(t.get("arguments")), str(t.get("observation"))) for t in st.get("tools") or [])
    pattern = ""
    if looped and len(calls) >= 4:
        tail = calls[-4:]
        names = {c[0] for c in tail}
        pattern = (f"{'/'.join(sorted(map(str, names)))} · args {'same' if len({c[1] for c in tail}) == 1 else 'distinct'}"
                   f" · output {'same' if len({c[2] for c in tail}) == 1 else 'distinct'}")
    return looped, pattern, steps


def frozen() -> set[tuple[str, str]]:
    if not FROZEN.is_file():
        return set()
    return {tuple(line.split()) for line in FROZEN.read_text(encoding="utf-8").splitlines() if len(line.split()) == 2}  # type: ignore[misc]


def delta(cells: dict[tuple[str, str], list[float]]) -> float:
    return statistics.mean(statistics.mean(cells[(t, "fixed")]) - statistics.mean(cells[(t, "legacy")]) for t in TASKS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    cold = frozen()
    summary: dict[str, Any] = {"frozen": sorted(" ".join(c) for c in cold), "executors": {}}
    for ex in ("strong", "weak"):
        cells: dict[tuple[str, str], list[float]] = {}
        meta: dict[str, dict[str, list[Any]]] = {a: {"looped": [], "usd": [], "steps": [], "reasons": []} for a in ARMS}
        for t in TASKS:
            for a in ARMS:
                vals = []
                for k in REPLICAS:
                    hid = f"brk-{a}-{ex}-r{k}"
                    if (t, hid) in cold:
                        continue
                    o = outcome_of(hid, t)
                    if o is None:
                        continue
                    vals.append(o)
                    looped, reason, steps = stop_of(hid, t)
                    meta[a]["looped"].append(looped)
                    meta[a]["steps"].append(steps)
                    if looped:
                        meta[a]["reasons"].append(reason)
                    u = usd_of(hid, t)
                    if u is not None:
                        meta[a]["usd"].append(u)
                cells[(t, a)] = vals
        if any(not v for v in cells.values()):
            print(f"\n===== executor: {ex} ===== incomplete: {[k for k, v in cells.items() if not v]}")
            continue
        d = delta(cells)
        rng = random.Random(SEED)
        draws = sorted(delta({k: rng.choices(v, k=len(v)) for k, v in cells.items()}) for _ in range(DRAWS))
        lo, hi = draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]
        floor = statistics.mean(statistics.stdev(cells[(t, "legacy")]) for t in TASKS if len(cells[(t, "legacy")]) >= 2)
        print(f"\n===== executor: {ex} =====")
        print(f"  solves legacy {sum(len(cells[(t, 'legacy')]) for t in TASKS)} · fixed {sum(len(cells[(t, 'fixed')]) for t in TASKS)}"
              f" · floor (legacy replica SD) {floor:.4f}")
        print(f"  score legacy {statistics.mean(statistics.mean(cells[(t, 'legacy')]) for t in TASKS):.4f}  "
              f"fixed {statistics.mean(statistics.mean(cells[(t, 'fixed')]) for t in TASKS):.4f}  "
              f"Δ {d:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]")
        for t in TASKS:
            print(f"    {t:<26} legacy {statistics.mean(cells[(t, 'legacy')]):.3f} fixed {statistics.mean(cells[(t, 'fixed')]):.3f}")
        for a in ARMS:
            m = meta[a]
            rate = sum(m["looped"]) / len(m["looped"]) if m["looped"] else float("nan")
            print(f"  {a:<6} breaker ended the run {sum(m['looped'])}/{len(m['looped'])} ({rate:.3f}) · "
                  f"US$/solve {statistics.mean(m['usd']) if m['usd'] else float('nan'):.4f} · steps {statistics.mean(m['steps']):.1f}")
            if m["reasons"]:
                print(f"         reasons: {sorted(set(m['reasons']))[:6]}")
        summary["executors"][ex] = {
            "delta": d, "ci95": [lo, hi], "floor": floor,
            "by_task": {t: {a: statistics.mean(cells[(t, a)]) for a in ARMS} for t in TASKS},
            "breaker_ended": {a: [sum(meta[a]["looped"]), len(meta[a]["looped"])] for a in ARMS},
            "usd_per_solve": {a: (statistics.mean(meta[a]["usd"]) if meta[a]["usd"] else None) for a in ARMS},
            "steps": {a: statistics.mean(meta[a]["steps"]) for a in ARMS},
            "reasons": {a: sorted(set(meta[a]["reasons"])) for a in ARMS},
        }
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
