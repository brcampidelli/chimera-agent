"""Run the interaction described in `PREREGISTRATION.md`: edit format x model family.

    python bench/edit_format_by_model/run.py

Reuses `bench/edit_tools` wholesale — the tasks, the materialiser, the runner and the verifier. That
is not laziness: a second copy of a verifier is a second definition of "passed", and the two drift.
The exclusion of `import_module_moved` is inherited from that bench's pre-registration for the same
reason, so it cannot be a choice made after seeing anything here.

Arms are FORCED through the denylist rather than suggested in a prompt. A nudge measures obedience;
denying the tool measures which format the model is cheaper in when it has no alternative.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EDIT_TOOLS_BENCH = HERE.parent / "edit_tools"
sys.path.insert(0, str(EDIT_TOOLS_BENCH))

from run_pilot import _last_receipt, _materialise, _measure, _run_solve, _verdict  # noqa: E402
from tasks import TASKS  # noqa: E402

#: Inherited from `bench/edit_tools/run_ab.py`, which dropped it because arm A failed it 3/3 and its
#: own pre-registration discards a task the control cannot solve.
EXCLUDED = {"import_module_moved"}

ARMS = {
    "P": {"CHIMERA_TOOL_DENYLIST": "write_file"},
    "W": {"CHIMERA_TOOL_DENYLIST": "edit_file,apply_patch,edit_batch"},
}

MODELS = [
    "openrouter/deepseek/deepseek-v4-flash-0731",
    "openrouter/z-ai/glm-5.3-flash",
    "openrouter/google/gemini-3.8-flash",
]

#: The tools each arm is supposed to have used, for the gate that checks an arm ran its condition.
EXPECTED = {"P": {"edit_file", "apply_patch"}, "W": {"write_file"}}
DENIED = {"P": {"write_file"}, "W": {"edit_file", "apply_patch", "edit_batch"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", default=str(HERE / "rows.json"))
    ap.add_argument("--limit-tasks", type=int, default=0, help="smoke: first N tasks only")
    ap.add_argument("--limit-models", type=int, default=0)
    args = ap.parse_args()

    tasks = [t for t in TASKS if t["name"] not in EXCLUDED]
    if args.limit_tasks:
        tasks = tasks[: args.limit_tasks]
    models = MODELS[: args.limit_models] if args.limit_models else MODELS
    rows: list[dict] = []
    total = len(tasks) * len(ARMS) * len(models)
    done = 0

    for model in models:
        for task in tasks:
            for arm, env in ARMS.items():
                done += 1
                work = Path(tempfile.mkdtemp(prefix=f"efm-{arm}-"))
                home, ws = work / "home", work / "ws"
                ws.mkdir(parents=True)
                _materialise(task, ws)
                started = time.time()
                code, tail = _run_solve(
                    task, ws, home, args.timeout,
                    extra_env={**env, "CHIMERA_DEFAULT_MODEL": model},
                )
                passed = _verdict(task, ws)
                receipt = _last_receipt(home / "runs.jsonl")
                edits, calls, tokens = _measure(receipt)
                names = {
                    n
                    for a in (receipt.get("attempts") or [])
                    for n in (a.get("tool_names") or [])
                }
                row = {
                    "model": model,
                    "arm": arm,
                    "task": task["name"],
                    "passed": bool(passed),
                    "completion_tokens": tokens,
                    "edit_calls": edits,
                    "tool_calls": calls,
                    "usd": receipt.get("usd") or 0.0,
                    "seconds": round(time.time() - started, 1),
                    "exit": code,
                    # The gate: an arm that used what it was denied did not run its condition.
                    "used_denied": sorted(names & DENIED[arm]),
                    "used_expected": sorted(names & EXPECTED[arm]),
                    "tail": tail[-160:],
                }
                rows.append(row)
                print(
                    f"  [{done:2}/{total}] {model.split('/')[-1][:26]:26} {arm} "
                    f"{task['name'][:24]:24} passou={row['passed']!s:5} "
                    f"tok={tokens:6} edits={edits:2} usd={row['usd']:.4f} {row['seconds']:.0f}s"
                    + ("  !! usou o negado" if row["used_denied"] else "")
                )
                shutil.rmtree(work, ignore_errors=True)
                Path(args.out).write_text(
                    json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
                )

    print(f"\n  {len(rows)} linhas em {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
