"""Re-grade 087's SURVIVING workspaces with pytest installed. No model calls, US$0.

    ~/hb-venv/bin/python bench/harness_bench/regrade_087.py

`preflight.py` found that 087's grader runs ``[sys.executable, "-m", "pytest", "tests"]`` at weight
0.25 of the 0.95 available, and that `~/hb-venv` had no pytest — so the check failed on 24 of 24
runs and that weight was scored as the agent's failure.

This answers what that cost **without paying for the agent's work twice**: the run's sandboxes
survive on disk, so the very directories the original left behind are re-graded by the same grader
in a fixed environment. The agent's work is unchanged and only the grader's environment differs,
which is what isolates the fix from run-to-run variance.

Measured: stored **0 of 24 passing**, mean 0.6050, max 0.6750 — against re-graded **19 of 24**,
mean 0.8030, max 0.9250. The five that still fail pytest fail it for real, so the task discriminates
rather than saturating. It was never a hard task; it was an unpassable one.

Needs the local `~/harness-bench` sandboxes, like everything else under this directory.
"""

from __future__ import annotations

import glob
import importlib.util
import json
import os
import re
import statistics
import sys
from pathlib import Path

TASK = "087-cli-parser-bug-tests"
HOME = Path(os.path.expanduser("~/harness-bench"))
GRADER = HOME / "tasks" / TASK / "oracle_grade.py"

spec = importlib.util.spec_from_file_location("oracle_grade_087", GRADER)
assert spec and spec.loader
grader = importlib.util.module_from_spec(spec)
sys.modules["oracle_grade_087"] = grader
spec.loader.exec_module(grader)

_ARM = re.compile(r"(arm-\d{3}-r\d)")


def stored(hid: str) -> float | None:
    hits = glob.glob(str(HOME / "data_try6" / "results" / hid / "*" / f"{TASK}.json"))
    if not hits:
        return None
    with open(hits[0], encoding="utf-8") as handle:
        payload = json.load(handle)
    value = (payload.get("oracle_result") or {}).get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def main() -> None:
    spaces = sorted(glob.glob(str(HOME / "data_try6" / "sandbox" / "*" / "*" / f"*{TASK}*" / "workspace")))
    seen: dict[str, str] = {}
    for path in spaces:
        found = _ARM.search(path)
        if found:
            seen.setdefault(found.group(1), path)
    print(f"workspaces found: {len(spaces)}   distinct arm-replica: {len(seen)}")

    rows = []
    for hid, path in sorted(seen.items()):
        before = stored(hid)
        try:
            result = grader.score_workspace(Path(path))
        except Exception as exc:  # noqa: BLE001 — a grader that dies on one workspace must not stop the sweep
            print(f"  {hid}  GRADER RAISED: {type(exc).__name__}: {exc}")
            continue
        after = float(result.get("outcome_score", float("nan")))
        checks = {c["id"]: c for c in result.get("checks", [])}
        pytest_ok = checks.get("pytest", {}).get("pass")
        rows.append((hid, before, after, pytest_ok))
        arrow = "->" if before is None else f"{before:.4f} ->"
        print(f"  {hid}  {arrow} {after:.4f}   pytest={pytest_ok}   {'PASS' if after >= 0.8 else 'fail'}")

    if not rows:
        return
    befores = [b for _h, b, _a, _p in rows if b is not None]
    afters = [a for _h, _b, a, _p in rows]
    print(f"\n  n={len(rows)}")
    print(f"  stored   mean {statistics.fmean(befores):.4f}  max {max(befores):.4f}"
          f"  passing(>=0.8) {sum(1 for b in befores if b >= 0.8)}")
    print(f"  regraded mean {statistics.fmean(afters):.4f}  max {max(afters):.4f}"
          f"  passing(>=0.8) {sum(1 for a in afters if a >= 0.8)}")
    print(f"  pytest check now passes in {sum(1 for r in rows if r[3])} of {len(rows)}")

    print(json.dumps(
        {"task": TASK, "rows": [{"hid": h, "stored": b, "regraded": a, "pytest": p} for h, b, a, p in rows]},
        indent=2, sort_keys=True,
    ))


if __name__ == "__main__":
    main()
