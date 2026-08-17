"""Stage 2 of `PREREGISTRATION.md` — the paired A/B, at the n stage 1 said was needed.

Arm A is today's tool surface. Arm B adds `edit_batch` (counted, multi-file, atomic validation) and
differs in nothing else: same tasks, same model, same verifier, same `max_steps`.

The pilot measured a pooled within-task sd of 0.82 edit calls and put the requirement at **3 paired
observations** for 80% power against the registered 2-call effect. This runs 11 tasks x 2 repeats =
22 pairs, comfortably past it, and the surplus buys a look at run-to-run noise rather than a second
chance at significance.

`import_module_moved` is EXCLUDED: arm A failed it 3/3 in the pilot, and the pre-registration
discards a task the control arm cannot solve. Its reference solution was executed by hand first, and
it passes — so the task is genuinely hard rather than broken, and the exclusion is about the arm, not
about a bug in the fixture.

    python bench/edit_tools/run_ab.py --repeats 2
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from run_pilot import _last_receipt, _materialise, _measure, _run_solve, _verdict  # noqa: E402
from tasks import TASKS  # noqa: E402

#: Dropped by the pre-registration's rule, not by preference. Named here so the exclusion travels
#: with the runner instead of living only in a results file nobody re-reads.
EXCLUDED = {"import_module_moved"}

ARMS = {"A": {}, "B": {"CHIMERA_EDIT_BATCH": "1"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=str(HERE / "results" / "ab.json"))
    args = ap.parse_args()

    tasks = [t for t in TASKS if t["name"] not in EXCLUDED]
    rows: list[dict] = []
    for task in tasks:
        for rep in range(args.repeats):
            for arm, env in ARMS.items():
                work = Path(tempfile.mkdtemp(prefix=f"ab-{arm}-{task['name']}-"))
                home, ws = work / "home", work / "ws"
                ws.mkdir()
                _materialise(task, ws)
                started = time.time()
                code, tail = _run_solve(task, ws, home, args.timeout, extra_env=env)
                passed = _verdict(task, ws)
                edits, calls, tokens = _measure(_last_receipt(home / "runs.jsonl"))
                used_batch = "edit_batch" in [
                    n
                    for a in (_last_receipt(home / "runs.jsonl").get("attempts") or [])
                    for n in (a.get("tool_names") or [])
                ]
                rows.append({
                    "task": task["name"], "family": task["family"], "arm": arm, "rep": rep,
                    "passed": passed, "exit": code, "edit_calls": edits, "tool_calls": calls,
                    "completion_tokens": tokens, "used_edit_batch": used_batch,
                    "seconds": round(time.time() - started, 1), "tail": tail,
                })
                print(f"  {task['name']:<28} {arm} rep{rep}  pass={passed!s:<5} "
                      f"edits={edits:<3} tok={tokens:<6} batch={used_batch}")
                shutil.rmtree(work, ignore_errors=True)

    # Pair on (task, rep). A pair counts only when BOTH arms solved it: how many edits a task takes
    # is undefined for a run that did not finish it, and scoring a failure as "cheap" would reward
    # the arm that gives up sooner.
    pairs: list[dict] = []
    for task in tasks:
        for rep in range(args.repeats):
            a = next((r for r in rows if r["task"] == task["name"] and r["rep"] == rep and r["arm"] == "A"), None)
            b = next((r for r in rows if r["task"] == task["name"] and r["rep"] == rep and r["arm"] == "B"), None)
            if a and b and a["passed"] and b["passed"]:
                pairs.append({
                    "task": task["name"], "rep": rep,
                    "d_edits": b["edit_calls"] - a["edit_calls"],
                    "d_tokens": b["completion_tokens"] - a["completion_tokens"],
                    "b_used_batch": b["used_edit_batch"],
                })

    def boot(values: list[int], reps: int = 10000) -> tuple[float, float]:
        """Bootstrap 95% CI of the median. Counts are small skewed integers; a normal-theory
        interval would be the wrong shape, which the pre-registration fixed in advance."""
        import random

        rng = random.Random(20260817)
        meds = sorted(
            statistics.median(rng.choices(values, k=len(values))) for _ in range(reps)
        )
        return meds[int(0.025 * reps)], meds[int(0.975 * reps)]

    out: dict[str, object] = {"runs": len(rows), "pairs": len(pairs), "rows": rows, "pair_rows": pairs}
    if pairs:
        de = [p["d_edits"] for p in pairs]
        dt = [p["d_tokens"] for p in pairs]
        lo, hi = boot(de)
        out["edit_calls"] = {
            "median_delta_B_minus_A": statistics.median(de),
            "mean_delta": round(statistics.fmean(de), 2),
            "ci95_median": [lo, hi],
            # The registered criterion, evaluated exactly as written and before the number was seen.
            "meets_registered_criterion": statistics.median(de) <= -2 and hi < 0,
        }
        lo_t, hi_t = boot(dt)
        out["completion_tokens"] = {
            "median_delta_B_minus_A": statistics.median(dt),
            "mean_delta": round(statistics.fmean(dt), 2),
            "ci95_median": [lo_t, hi_t],
        }
        out["arm_b_actually_used_the_tool"] = f"{sum(p['b_used_batch'] for p in pairs)}/{len(pairs)}"

    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    print(f"\n  {len(pairs)} usable pairs out of {len(rows) // 2} attempted")
    if pairs:
        ec = out["edit_calls"]
        print(f"  edit calls  B-A: median {ec['median_delta_B_minus_A']:+} "  # type: ignore[index]
              f"CI95 {ec['ci95_median']}  -> criterion met: {ec['meets_registered_criterion']}")  # type: ignore[index]
        tc = out["completion_tokens"]
        print(f"  completion tokens B-A: median {tc['median_delta_B_minus_A']:+} CI95 {tc['ci95_median']}")  # type: ignore[index]
        print(f"  arm B reached for edit_batch in {out['arm_b_actually_used_the_tool']} pairs")
    print(f"  -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
