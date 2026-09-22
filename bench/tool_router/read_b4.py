"""Read B4's oracle scores, receipts and router statistics into the analysis of PREREGISTRATION §5.

Run in WSL:  ~/hb-venv-b4/bin/python read_b4.py [--home ~/harness-bench] [--json <out.json>]

Per (task, arm, executor, replica):
  outcome_score  <- data_try6/results/<hid>/*/<task>.json : oracle_result.outcome_score
  usd, steps     <- ~/hb-homes/<task>-<hid>/runs.jsonl (rounds summed)
  router stats   <- ~/hb-homes/<task>-<hid>/tool_router.jsonl (absent on the control, by design)

Then, per executor: the paired off/on difference over tasks with a bootstrap CI over TASKS, beside
the control arm's own replica-to-replica SD — the floor this run measures for itself, because the
grading environment is not #453's (PREREGISTRATION §4). Nothing here spends.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics
from pathlib import Path

TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary", "039-repo-architecture-map",
    "040-test-coverage-fill", "041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety",
    "044-ci-config-repair", "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug", "085-flaky-test-root-cause",
    "086-sql-migration-preflight-rollback", "087-cli-parser-bug-tests",
    "089-ab-test-caveat-analysis", "092-schema-drift-audit", "094-metric-definition-migration-diff",
]
EXECUTORS = ("strong", "weak")
ARMS = ("off", "on")
REPLICAS = (0, 1, 2)
CHAINED = {"011-code-debug"}
"""The multi-round task — the trade-off the hypothesis itself predicts (P5), read separately."""
SEED = 20260922


def hid(arm: str, ex: str, k: int) -> str:
    return f"sys1-{arm}-{ex}-r{k}"


def outcome_of(home: Path, h: str, task: str) -> float | None:
    hits = glob.glob(str(home / "data_try6" / "results" / h / "*" / f"{task}.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as rf:
        d = json.load(rf)
    v = (d.get("oracle_result") or {}).get("outcome_score")
    return float(v) if isinstance(v, (int, float)) else None


def receipt_of(h: str, task: str) -> dict:
    p = Path(os.path.expanduser(f"~/hb-homes/{task}-{h}/runs.jsonl"))
    if not p.is_file():
        return {}
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        return {}
    return {
        "usd": sum(float(r.get("usd") or 0.0) for r in lines),
        "ending": lines[-1].get("ending"),
        "rounds": len(lines),
    }


def steps_of(h: str, task: str) -> int | None:
    """The executor's step count, from the step log.

    Not from `runs.jsonl`: an attempt there carries tokens, diffs and tool names but no step count,
    so a reader that looks for one finds `None` and reports "no pairs" — which reads as "steps did
    not change" when nothing was measured. `traces.jsonl` holds one row per worker run with a
    `steps` LIST, and a multi-round task writes one row per round, so the rounds are summed."""
    p = Path(os.path.expanduser(f"~/hb-homes/{task}-{h}/traces.jsonl"))
    if not p.is_file():
        return None
    total = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        total += len(json.loads(line).get("steps") or [])
    return total or None


def router_of(h: str, task: str) -> dict:
    p = Path(os.path.expanduser(f"~/hb-homes/{task}-{h}/tool_router.jsonl"))
    if not p.is_file():
        return {}
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not rows:
        return {}
    out = {k: sum(int(r.get(k) or 0) for r in rows) for k in ("calls", "narrowed", "answered", "fallbacks")}
    out["usd"] = sum(float(r.get("usd") or 0.0) for r in rows)
    picks: dict[str, int] = {}
    for r in rows:
        for name, n in (r.get("picks") or {}).items():
            picks[name] = picks.get(name, 0) + int(n)
    out["picks"] = picks
    return out


def boot_ci(xs: list[float], n: int = 10000) -> tuple[float, float]:
    if len(xs) < 2:
        return float("nan"), float("nan")
    rng = random.Random(SEED)
    draws = sorted(statistics.mean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return draws[int(0.025 * n)], draws[min(int(0.975 * n), n - 1)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", type=Path, default=Path(os.path.expanduser("~/harness-bench")))
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    cells: dict = {}
    for ex in EXECUTORS:
        for arm in ARMS:
            for k in REPLICAS:
                h = hid(arm, ex, k)
                for task in TASKS:
                    cells[(task, arm, ex, k)] = {
                        "outcome": outcome_of(args.home, h, task),
                        **receipt_of(h, task),
                        "steps": steps_of(h, task),
                        "router": router_of(h, task),
                    }
    scored = [v for v in cells.values() if v["outcome"] is not None]
    print(f"cells with an outcome: {len(scored)} / {len(cells)}")

    summary: dict = {"executors": {}}
    for ex in EXECUTORS:
        print(f"\n{'=' * 12} executor: {ex} {'=' * 12}")

        def mean_of(task: str, arm: str, field: str, ex: str = ex) -> float | None:
            vals = [
                cells[(task, arm, ex, k)][field]
                for k in REPLICAS
                if cells[(task, arm, ex, k)].get(field) is not None
            ]
            return statistics.mean(vals) if vals else None

        # The floor this run measures for itself: replica-to-replica SD of the CONTROL arm.
        sds = [
            statistics.stdev(vals)
            for task in TASKS
            if len(vals := [cells[(task, "off", ex, k)]["outcome"] for k in REPLICAS
                            if cells[(task, "off", ex, k)]["outcome"] is not None]) >= 2
        ]
        floor = statistics.mean(sds) if sds else float("nan")
        print(f"  replica SD of the control arm (the floor, measured here): {floor:.4f} over {len(sds)} tasks")

        rows = []
        for task in TASKS:
            off, on = mean_of(task, "off", "outcome"), mean_of(task, "on", "outcome")
            if off is None or on is None:
                continue
            rows.append((task, off, on, mean_of(task, "off", "usd"), mean_of(task, "on", "usd"),
                         mean_of(task, "off", "steps"), mean_of(task, "on", "steps")))
        if not rows:
            print("  no task has both arms scored yet")
            continue
        deltas = [on - off for _, off, on, *_ in rows]
        lo, hi = boot_ci(deltas)
        pass_off = statistics.mean(1.0 if off >= 0.8 else 0.0 for _, off, _, *_ in rows) if rows else float("nan")
        pass_on = statistics.mean(1.0 if on >= 0.8 else 0.0 for _, _, on, *_ in rows) if rows else float("nan")
        print(f"  tasks compared: {len(rows)}")
        print(f"  outcome  off {statistics.mean(r[1] for r in rows):.4f}  on {statistics.mean(r[2] for r in rows):.4f}"
              f"   Δ {statistics.mean(deltas):+.4f}  95% CI over tasks [{lo:+.4f}, {hi:+.4f}]"
              f"   (floor {floor:.4f})")
        print(f"  pass@0.8 off {pass_off:.3f}  on {pass_on:.3f}")

        def paired(idx_off: int, idx_on: int, label: str, rows: list = rows) -> dict:
            pairs = [(r[idx_off], r[idx_on]) for r in rows if r[idx_off] is not None and r[idx_on] is not None]
            if not pairs:
                print(f"  {label}: no pairs")
                return {}
            d = [b - a for a, b in pairs]
            rel = [(b - a) / a for a, b in pairs if a]
            clo, chi = boot_ci(d)
            print(f"  {label:7s} off {statistics.mean(a for a, _ in pairs):.4f}  on {statistics.mean(b for _, b in pairs):.4f}"
                  f"   Δ {statistics.mean(d):+.4f}  95% CI [{clo:+.4f}, {chi:+.4f}]"
                  f"   relative {statistics.mean(rel) * 100:+.1f}%  (n={len(pairs)})")
            return {"off": statistics.mean(a for a, _ in pairs), "on": statistics.mean(b for _, b in pairs),
                    "delta": statistics.mean(d), "ci95": [clo, chi],
                    "relative_pct": statistics.mean(rel) * 100, "n": len(pairs)}

        usd = paired(3, 4, "usd")
        steps = paired(5, 6, "steps")

        # How much the router acted, and what it cost of the total.
        r_calls = r_narrow = r_fall = r_answer = 0
        r_usd = 0.0
        picks: dict[str, int] = {}
        for task in TASKS:
            for k in REPLICAS:
                rt = cells[(task, "on", ex, k)]["router"]
                if not rt:
                    continue
                r_calls += rt["calls"]
                r_narrow += rt["narrowed"]
                r_fall += rt["fallbacks"]
                r_answer += rt["answered"]
                r_usd += rt["usd"]
                for name, n in rt["picks"].items():
                    picks[name] = picks.get(name, 0) + n
        on_usd_total = sum(v["usd"] for (t, a, e, _k), v in cells.items()
                           if a == "on" and e == ex and v.get("usd") is not None)
        share = (r_usd / on_usd_total * 100) if on_usd_total else float("nan")
        print(f"  router: {r_calls} calls · narrowed {r_narrow} · answered {r_answer} · fallbacks {r_fall}"
              f" · US$ {r_usd:.4f} = {share:.1f}% of the arm's US$ {on_usd_total:.4f}")
        print(f"  picks: {dict(sorted(picks.items(), key=lambda kv: -kv[1])[:8])}")

        chained = [(r[0], r[1], r[2]) for r in rows if r[0] in CHAINED]
        for task, off, on in chained:
            print(f"  chained {task}: off {off:.3f} on {on:.3f} Δ {on - off:+.3f}")

        # The solves whose control arm never ran the router are the negative control.
        leaked = [k for k, v in cells.items() if k[1] == "off" and k[2] == ex and v["router"]]
        print(f"  control arms carrying a router receipt (must be 0): {len(leaked)}")

        summary["executors"][ex] = {
            "floor_replica_sd": floor, "tasks": len(rows),
            "outcome": {"off": statistics.mean(r[1] for r in rows), "on": statistics.mean(r[2] for r in rows),
                        "delta": statistics.mean(deltas), "ci95": [lo, hi]},
            "pass_at_0.8": {"off": pass_off, "on": pass_on},
            "usd": usd, "steps": steps,
            "router": {"calls": r_calls, "narrowed": r_narrow, "answered": r_answer, "fallbacks": r_fall,
                       "usd": r_usd, "share_pct": share, "picks": picks},
            "chained": {t: {"off": o, "on": n} for t, o, n in chained},
            "control_router_leak": len(leaked),
        }

    if args.json:
        args.json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
