"""Read the factorial's oracle scores + receipts into the analysis of PREREGISTRATION.md §5.

Run in WSL:  ~/hb-venv/bin/python read_results.py [--home ~/harness-bench] [--out <RESULTS-fragment>]

Per (task, arm, replica) it reads:
  outcome_score  <- data_try6/results/<hid>/*/<task>.json : oracle_result.outcome_score  (the primary DV)
  usd            <- ~/hb-homes/<task>-<hid>/runs.jsonl : sum of every line's `usd` (rounds summed)
  ending         <- that file's last line's `ending`  (loop self-report; secondary)
  security_score <- the result json's scoring.security_score

Then: per-task mean over k replicas, factorial main effects (A repo-map, B checklist, C planner) and
two-factor interactions paired by task with a bootstrap CI over the 25 tasks; the within-cell SD and
the thresholded flip-rate/ICC noise floor from `chimera.eval.replicated`; USD per arm. Nothing here
spends; it only reads what the run wrote.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from itertools import product
from pathlib import Path

TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary", "039-repo-architecture-map",
    "040-test-coverage-fill", "041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety",
    "044-ci-config-repair", "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "078-local-api-cursor-retry-ledger", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug", "085-flaky-test-root-cause",
    "086-sql-migration-preflight-rollback", "087-cli-parser-bug-tests", "088-api-contract-mock-client-compat",
    "089-ab-test-caveat-analysis", "092-schema-drift-audit", "094-metric-definition-migration-diff",
]
BITS = [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]
REPLICAS = (0, 1, 2)


def hid(a: int, b: int, c: int, k: int) -> str:
    return f"arm-{a}{b}{c}-r{k}"


def outcome_of(home: Path, h: str, task: str) -> float | None:
    hits = glob.glob(str(home / "data_try6" / "results" / h / "*" / f"{task}.json"))
    if not hits:
        return None
    d = json.load(open(hits[0], encoding="utf-8"))
    o = d.get("oracle_result") or {}
    v = o.get("outcome_score")
    return float(v) if isinstance(v, (int, float)) else None


def receipt_of(h: str, task: str) -> tuple[float | None, str | None]:
    """(summed usd over rounds, last ending) or (None, None) if no receipt."""
    p = Path(os.path.expanduser(f"~/hb-homes/{task}-{h}/runs.jsonl"))
    if not p.is_file():
        return None, None
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        return None, None
    usd = sum(float(r.get("usd") or 0.0) for r in lines)
    return usd, lines[-1].get("ending")


def collect(home: Path) -> dict:
    cells: dict = {}
    for (a, b, c), k in product(BITS, REPLICAS):
        h = hid(a, b, c, k)
        for task in TASKS:
            usd, ending = receipt_of(h, task)
            cells[(task, (a, b, c), k)] = {
                "outcome": outcome_of(home, h, task), "usd": usd, "ending": ending,
            }
    return cells


def _pass(x: float | None, thr: float = 0.8) -> bool:
    return x is not None and x >= thr


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", type=Path, default=Path(os.path.expanduser("~/harness-bench")))
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    cells = collect(args.home)

    scored = [(k, v) for k, v in cells.items() if v["outcome"] is not None]
    print(f"cells with an outcome: {len(scored)} / {len(cells)} (missing = not yet run or receiptless)")

    # per-task mean per arm
    per_task_arm: dict = {}
    for (task, bits, k), v in cells.items():
        if v["outcome"] is None:
            continue
        per_task_arm.setdefault((task, bits), []).append(v["outcome"])
    arm_task_mean = {(t, b): statistics.mean(vs) for (t, b), vs in per_task_arm.items()}

    def effect(factor_idx: int) -> tuple[float, list[float]]:
        per_task_delta = []
        for task in TASKS:
            withs = [arm_task_mean[(task, b)] for b in BITS if b[factor_idx] == 1 and (task, b) in arm_task_mean]
            withouts = [arm_task_mean[(task, b)] for b in BITS if b[factor_idx] == 0 and (task, b) in arm_task_mean]
            if withs and withouts:
                per_task_delta.append(statistics.mean(withs) - statistics.mean(withouts))
        return (statistics.mean(per_task_delta) if per_task_delta else float("nan")), per_task_delta

    def boot_ci(xs: list[float], n: int = 10000, seed: int = 20260912) -> tuple[float, float]:
        import random
        if not xs:
            return float("nan"), float("nan")
        rng = random.Random(seed)
        means = sorted(statistics.mean(rng.choice(xs) for _ in xs) for _ in range(n))
        return means[int(n * 0.025)], means[int(n * 0.975)]

    print("\n=== Main effects (paired by task, bootstrap 95% CI over tasks) ===")
    for idx, name in ((0, "A repo-map"), (1, "B checklist"), (2, "C planner")):
        eff, deltas = effect(idx)
        lo, hi = boot_ci(deltas)
        print(f"  {name:<12} Δ={eff:+.3f}  95% [{lo:+.3f}, {hi:+.3f}]  (n_tasks={len(deltas)})")

    # noise floor: within-cell SD (needs k>=2 present), and thresholded flip rate
    sds, flips = [], []
    for (a, b, c) in BITS:
        for task in TASKS:
            vals = [cells[(task, (a, b, c), k)]["outcome"] for k in REPLICAS if cells[(task, (a, b, c), k)]["outcome"] is not None]
            if len(vals) >= 2:
                sds.append(statistics.pstdev(vals))
                passes = [_pass(v) for v in vals]
                flips.append(0 if all(passes) or not any(passes) else 1)
    if sds:
        print(f"\n=== Noise floor ===\n  mean within-cell SD = {statistics.mean(sds):.3f} (n_cells={len(sds)})")
        print(f"  thresholded (>=0.8) flip rate = {statistics.mean(flips):.2f}")

    # cost per arm
    print("\n=== USD per arm (sum over its solves) ===")
    for (a, b, c) in BITS:
        us = [cells[(task, (a, b, c), k)]["usd"] for task in TASKS for k in REPLICAS if cells[(task, (a, b, c), k)]["usd"] is not None]
        if us:
            print(f"  arm-{a}{b}{c}: n={len(us):>3} totalUSD={sum(us):.3f} mean/solve={statistics.mean(us):.4f}")
    total = sum(v["usd"] for v in cells.values() if v["usd"] is not None)
    print(f"\n  total spend so far: US$ {total:.2f}")

    # self-report vs oracle (secondary)
    agree = tot = 0
    for v in cells.values():
        if v["outcome"] is None or v["ending"] is None:
            continue
        tot += 1
        agree += int((v["ending"] == "success") == _pass(v["outcome"]))
    if tot:
        print(f"\n  loop `ending`==success vs oracle>=0.8 agreement: {agree}/{tot} = {agree / tot:.2f}")


if __name__ == "__main__":
    main()
