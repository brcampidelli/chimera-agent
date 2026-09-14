"""#453's three main effects, recomputed with 087's corrected scores overlaid.

Same analysis as `read_results.py` — per-task mean per arm, paired by task, bootstrap over tasks.
The only difference is the 24 cells of 087, which now carry what the grader says when it can run.
Nothing is written back into the harness's result files: the record of what the run produced stays
as it was, and this is the corrected READING of it.
"""

from __future__ import annotations

import glob
import json
import os
import random
import statistics
from itertools import product
from pathlib import Path

HOME = Path(os.path.expanduser("~/harness-bench"))
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
BITS = [(a, b, c) for a in (0, 1) for b in (0, 1) for c in (0, 1)]
REPLICAS = (0, 1, 2)

# Re-graded on the surviving workspaces with pytest present. US$0, no model calls.
REGRADED_087 = {
    "arm-000-r0": 0.9107, "arm-000-r1": 0.9250, "arm-000-r2": 0.9107,
    "arm-001-r0": 0.5167, "arm-001-r1": 0.9107, "arm-001-r2": 0.8250,
    "arm-010-r0": 0.9107, "arm-010-r1": 0.9250, "arm-010-r2": 0.8250,
    "arm-011-r0": 0.5167, "arm-011-r1": 0.8250, "arm-011-r2": 0.9107,
    "arm-100-r0": 0.2867, "arm-100-r1": 0.9107, "arm-100-r2": 0.9107,
    "arm-101-r0": 0.9107, "arm-101-r1": 0.8964, "arm-101-r2": 0.6607,
    "arm-110-r0": 0.8250, "arm-110-r1": 0.2867, "arm-110-r2": 0.9250,
    "arm-111-r0": 0.9107, "arm-111-r1": 0.9107, "arm-111-r2": 0.9250,
}


def stored(hid: str, task: str) -> float | None:
    hits = glob.glob(str(HOME / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as handle:
        payload = json.load(handle)
    value = (payload.get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def cells(corrected: bool) -> dict:
    out = {}
    for (a, b, c), k in product(BITS, REPLICAS):
        hid = f"arm-{a}{b}{c}-r{k}"
        for task in TASKS:
            value = stored(hid, task)
            if corrected and task == "087-cli-parser-bug-tests" and hid in REGRADED_087:
                value = REGRADED_087[hid]
            if value is not None:
                out[(task, (a, b, c), k)] = value
    return out


def effects(grid: dict) -> dict[str, tuple[float, float, float, int]]:
    per_task_arm: dict = {}
    for (task, bits, _k), value in grid.items():
        per_task_arm.setdefault((task, bits), []).append(value)
    mean = {key: statistics.fmean(vs) for key, vs in per_task_arm.items()}

    def boot(xs: list[float], n: int = 10000, seed: int = 20260912) -> tuple[float, float]:
        rng = random.Random(seed)
        draws = sorted(statistics.fmean(rng.choice(xs) for _ in xs) for _ in range(n))
        return draws[int(n * 0.025)], draws[int(n * 0.975)]

    out = {}
    for index, name in ((0, "A repo-map"), (1, "B checklist"), (2, "C planner")):
        deltas = []
        for task in TASKS:
            withs = [mean[(task, b)] for b in BITS if b[index] == 1 and (task, b) in mean]
            withouts = [mean[(task, b)] for b in BITS if b[index] == 0 and (task, b) in mean]
            if withs and withouts:
                deltas.append(statistics.fmean(withs) - statistics.fmean(withouts))
        low, high = boot(deltas)
        out[name] = (statistics.fmean(deltas), low, high, len(deltas))
    return out


def main() -> None:
    before, after = effects(cells(False)), effects(cells(True))
    print(f"{'effect':<13} {'published':>26}   {'with 087 corrected':>26}")
    for name in before:
        d0, l0, h0, n0 = before[name]
        d1, l1, h1, n1 = after[name]
        print(f"{name:<13} {d0:+.3f} [{l0:+.3f}, {h0:+.3f}]   {d1:+.3f} [{l1:+.3f}, {h1:+.3f}]  (n={n1})")
    print("\n  every interval still spans zero:",
          all(low < 0 < high for _d, low, high, _n in after.values()))


if __name__ == "__main__":
    main()
