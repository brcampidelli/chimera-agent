"""Read the planner A/B (PREREGISTRATION.md sections 3-5) and print the verdict by the registered rule.

  python bench/planner_ab/read_ab.py            # from the repository root; reads only committed files

E1 (deepseek-v3.2) comes from the stored factorial rows, arms 000 (no-plan) against 001 (plan).
E2/E3 come from `bench/planner_ab/results/<tag>.jsonl`, the driver log copied out of WSL.
Nothing here spends and nothing is tuned: margin, threshold and interval are the registration's.
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from _legacy_defaults import pin_legacy_defaults  # noqa: E402

from chimera.eval.proportions import mean_t_interval  # noqa: E402

HERE = Path(__file__).resolve().parent
FACTORIAL = ROOT / "bench" / "harness_bench" / "results" / "2026-09-13-factorial.jsonl"
PASS = 0.8
MARGIN = -0.05
ALPHA_TOST = 0.10  # one-sided 0.05 == lower limit of the two-sided 90% interval
GATE = (0.30, 0.95)
FAMILY = {"e1": "DeepSeek", "glm": "Zhipu", "qwen": "Alibaba"}


def load_e1() -> list[dict]:
    rows = []
    for line in FACTORIAL.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if (r["repo_map"], r["checklist"]) == (0, 0):
            rows.append({"task": r["task"], "arm": "plan" if r["planner"] else "noplan",
                         "outcome": r["outcome"], "usd": r["usd"], "ok": r["outcome"] is not None})
    return rows


def load_driver(tag: str) -> list[dict]:
    path = HERE / "results" / f"{tag}.jsonl"
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        rows.append({"task": r["task"], "arm": r["arm"], "outcome": r["outcome"],
                     "usd": r.get("real_usd"), "ok": r["rc"] == 0 and r["outcome"] is not None})
    return rows


def read(tag: str, rows: list[dict]) -> dict:
    by: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["outcome"] is not None:
            by[r["task"]][r["arm"]].append(r)
    d_pass, d_score, tasks = [], [], []
    for t, arms in sorted(by.items()):
        if "plan" not in arms or "noplan" not in arms:
            continue
        pr = {a: statistics.mean(1.0 if x["outcome"] >= PASS else 0.0 for x in arms[a]) for a in arms}
        sc = {a: statistics.mean(x["outcome"] for x in arms[a]) for a in arms}
        d_pass.append(pr["noplan"] - pr["plan"])
        d_score.append(sc["noplan"] - sc["plan"])
        tasks.append(t)
    plan_pass = [1.0 if r["outcome"] >= PASS else 0.0 for r in rows if r["arm"] == "plan" and r["outcome"] is not None]
    usd = {a: [r["usd"] for r in rows if r["arm"] == a and r["usd"] is not None] for a in ("plan", "noplan")}
    m, lo, hi = mean_t_interval(d_pass, ALPHA_TOST)
    ms, los, his = mean_t_interval(d_score, ALPHA_TOST)
    n_ok = sum(r["ok"] for r in rows)
    informative = bool(plan_pass) and GATE[0] <= statistics.mean(plan_pass) <= GATE[1] and n_ok / len(rows) >= 0.95
    cost_ratio = (statistics.mean(usd["noplan"]) / statistics.mean(usd["plan"])) if usd["plan"] and usd["noplan"] else float("nan")
    return {
        "tag": tag, "n_tasks": len(tasks), "n_solves": len(rows), "ok_frac": n_ok / len(rows) if rows else 0.0,
        "plan_pass_rate": statistics.mean(plan_pass) if plan_pass else float("nan"),
        "noplan_pass_rate": statistics.mean(1.0 if r["outcome"] >= PASS else 0.0 for r in rows if r["arm"] == "noplan" and r["outcome"] is not None),
        "d_pass": (m, lo, hi), "d_score": (ms, los, his), "informative": informative,
        "noninferior": lo > MARGIN, "cost_ratio": cost_ratio,
        "worse": sum(d < 0 for d in d_pass), "equal": sum(d == 0 for d in d_pass), "better": sum(d > 0 for d in d_pass),
    }


def main() -> None:
    results = [read("e1", load_e1())]
    for tag in ("glm", "qwen"):
        rows = load_driver(tag)
        if rows:
            results.append(read(tag, rows))
    for r in results:
        m, lo, hi = r["d_pass"]
        ms, los, his = r["d_score"]
        print(f"[{r['tag']}] {FAMILY[r['tag']]}: tasks={r['n_tasks']} solves={r['n_solves']} ok={r['ok_frac']:.3f} "
              f"informative={r['informative']}")
        print(f"   pass rate plan={r['plan_pass_rate']:.3f} noplan={r['noplan_pass_rate']:.3f}")
        print(f"   d(noplan-plan) pass  mean={m:+.4f}  90% t [{lo:+.4f}, {hi:+.4f}]  non-inferior(>-0.05)={r['noninferior']}")
        print(f"   d(noplan-plan) score mean={ms:+.4f}  90% t [{los:+.4f}, {his:+.4f}]")
        print(f"   tasks noplan worse/equal/better = {r['worse']}/{r['equal']}/{r['better']};  cost noplan/plan = {r['cost_ratio']:.3f}")
        if r["tag"] == "glm":
            print("   GLM's recorded ratio omits the single-model planner's cost and covers only metered calls.")
    inf = [r for r in results if r["informative"]]
    fams = {FAMILY[r["tag"]] for r in inf}
    cond1 = len(fams) >= 2
    cond2 = all(r["noninferior"] for r in inf) and bool(inf)
    cond3 = all(r["cost_ratio"] <= 1.05 for r in inf) and bool(inf)
    print(f"\nconditions: >=2 families={cond1} ({sorted(fams)})  non-inferior on every informative executor={cond2}  "
          f"recorded no-plan cost not dearer by >5%={cond3}")
    print("VERDICT:", "ADOPT planner-off when the run has a verifier" if cond1 and cond2 and cond3 else "NOT ADOPTED")


if __name__ == "__main__":
    pin_legacy_defaults()  # the five 2026-11-10 defaults, back to the value the factorial used
    main()
