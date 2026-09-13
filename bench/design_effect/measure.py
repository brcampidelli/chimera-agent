"""Measure OUR intra-task correlation, and price what a replica actually buys.

Study 18 shortlist item #5. Cost **US$0**: it reads the Harness-Bench factorial (#453), whose
23 tasks x 8 arms x 3 replicas is exactly the grid ICC(1) needs.

The point of doing this rather than importing the paper's number: `2609.06386` measured 0.530 on
its own corpus, and a correlation is a property of a corpus, not a constant. Ours is higher.

Uses the SHIPPED statistics — `chimera.eval.replicated.icc1` and `design_effect` — not local
reimplementations, for the same reason `bench/claim_vs_diff` imports #457's arm: a number that has
to match an existing one must come out of the existing code. `design_effect` landed in that module
rather than here precisely so the app prints it too.

    python measure.py        # every table in RESULTS.md
"""

from __future__ import annotations

import glob
import json
import os
import statistics
from pathlib import Path

from chimera.eval.replicated import design_effect, icc1

HERE = Path(__file__).resolve().parent

TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary",
    "039-repo-architecture-map", "040-test-coverage-fill", "041-frontend-state-bug",
    "042-api-schema-migration", "043-db-migration-safety", "044-ci-config-repair",
    "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug",
    "085-flaky-test-root-cause", "086-sql-migration-preflight-rollback",
    "087-cli-parser-bug-tests", "089-ab-test-caveat-analysis", "092-schema-drift-audit",
    "094-metric-definition-migration-diff",
]
ARMS = [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]
REPLICAS = (0, 1, 2)
PASS_THRESHOLD = 0.8
PAPER_ICC = 0.530
"""arXiv 2609.06386, n=24,998 groups. Reported for contrast, never used as our number."""


def _outcome(bench_home: Path, hid: str, task: str) -> float | None:
    hits = glob.glob(str(bench_home / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as handle:
        payload = json.load(handle)
    value = (payload.get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def measure(bench_home: Path | None = None) -> dict:
    bench_home = bench_home or Path(os.path.expanduser("~/harness-bench"))
    per_arm: dict[str, float] = {}
    skipped: dict[str, str] = {}
    for a, b, c in ARMS:
        grid: list[list[bool]] = []
        for task in TASKS:
            row = [_outcome(bench_home, f"arm-{a}{b}{c}-r{k}", task) for k in REPLICAS]
            if all(v is not None for v in row):
                grid.append([v >= PASS_THRESHOLD for v in row])  # type: ignore[operator]
        icc, why = icc1(grid)
        name = f"arm-{a}{b}{c}"
        if icc is None:
            skipped[name] = why
        else:
            per_arm[name] = icc

    values = sorted(per_arm.values())
    median = statistics.median(values)
    return {
        "per_arm_icc": {k: round(v, 4) for k, v in sorted(per_arm.items())},
        "skipped": skipped,
        "icc_median": round(median, 4),
        "icc_min": round(min(values), 4),
        "icc_max": round(max(values), 4),
        "paper_icc": PAPER_ICC,
        "design_effect_at_k3": round(design_effect(median, 3), 4),
        "effective_per_task_runs": {
            str(m): round(m / design_effect(median, m), 4) for m in (1, 2, 3, 5, 8)
        },
    }


def main() -> None:
    report = measure()
    median = report["icc_median"]

    print("-- our own ICC(1), per factorial arm (23 tasks x 3 replicas each) --")
    for name, value in report["per_arm_icc"].items():
        deff = design_effect(value, 3)
        print(f"  {name}   ICC {value:+.4f}   DEFF {deff:.3f}   69 runs -> {69 / deff:.1f} effective")
    for name, why in report["skipped"].items():
        print(f"  {name}   not computable: {why}")

    print(
        f"\n  median {median:.4f}  (min {report['icc_min']:.4f}, max {report['icc_max']:.4f})"
        f"   — the paper's corpus: {PAPER_ICC}"
    )

    print("\n-- what a replica buys, at OUR correlation --")
    print(f"  {'k':>3s} {'runs':>5s} {'DEFF':>7s} {'effective':>10s} {'the k-th run adds':>18s}")
    previous = 0.0
    for k in (1, 2, 3, 5, 8):
        deff = design_effect(median, k)
        effective = k / deff
        print(f"  {k:>3d} {k:>5d} {deff:>7.3f} {effective:>10.3f} {effective - previous:>18.3f}")
        previous = effective

    print("\n-- the same 552 solves, spent differently --")
    print(f"  {'shape':>26s} {'clusters':>9s} {'effective per arm':>18s}")
    for tasks, k, note in (
        (23, 3, "what #453 ran"),
        (35, 2, ""),
        (69, 1, "same money, 106 tasks existed"),
        (8, 3, "+ 45 tasks at k=1 — floor AND breadth"),
    ):
        if tasks == 8:
            effective = 8 * 3 / design_effect(median, 3) + 45
            clusters = 53
        else:
            effective = tasks * k / design_effect(median, k)
            clusters = tasks
        print(f"  {f'{tasks} tasks x {k}':>26s} {clusters:>9d} {effective:>18.1f}   {note}")

    (HERE / "results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    print("\nwrote results.json")


if __name__ == "__main__":
    main()
