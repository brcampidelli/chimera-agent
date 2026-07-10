"""M18-1 gate A/B — coverage-grade vs spec-grounded generated tests, in the NO-`--verify` regime.

The claim under test (arXiv 2607.06636): when `solve` has no user test command, the fitness gate
falls back to an LLM judging requirement *coverage* — a proxy that rubber-stamps wrong code. Grounding
*executable* test generation in the extracted requirements should catch bugs the coverage grade
misses, i.e. LOWER the false-positive rate (gate says "success" on code that a hidden test fails).

Honest by construction:
- The two arms differ ONLY in the gate: `--checklist` (LLM coverage grade) vs `--gen-tests` (executable
  spec tests). Same model, same scaffolding, same start state, paired (fresh workspace per arm).
- Neither arm ever sees the task's hidden test — it is written into the workspace ONLY after solve, to
  grade. So `--gen-tests` cannot copy the ground truth, and the coverage grade cannot peek at it.
- Two numbers per arm: the independent resolve rate (hidden pytest passes) and the false-positive rate
  (solve reported "success" but the hidden test fails). The paired McNemar/Wilson verdict is on the
  independent grade. solve's self-report is used ONLY to measure how often the gate lied.

Env: BENCH_MODEL, BENCH_TIMEOUT (s/arm), BENCH_TASKS (comma ids). Predictions are pre-registered in
RESULTS.md before running — do not re-roll for significance.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 can't encode Δ

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent.parent))
from tasks import TASKS  # noqa: E402

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/mistralai/mistral-small-3.2-24b-instruct")
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "300"))
_ONLY = {t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()}
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills"]
_SCAFFOLD = ["--repo-map", "--progress-ledger", "--replan", "--max-attempts", "3", *_HYGIENE]
# Both arms: NO --verify (the regime where the proxy gate matters). Only the gate flag differs.
_ARM_FLAGS = {
    "coverage": ["--checklist", *_SCAFFOLD],   # gate = LLM requirement-coverage grade
    "gentests": ["--gen-tests", *_SCAFFOLD],   # gate = executable spec-grounded tests
}
_SELF = re.compile(r"\b(success|failed) after", re.IGNORECASE)


def _starter_workspace(task: dict, root: Path) -> Path:
    """Fresh workspace with ONLY the starter files — the task's hidden test is withheld."""
    ws = root / f"{task['id']}"
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return ws


def _solve(task: dict, ws: Path, arm: str) -> bool:
    """Run one arm; return solve's SELF-reported success (parsed from its output)."""
    argv = ["chimera", "solve", task["prompt"], "--workspace", str(ws), "--model", _MODEL, *_ARM_FLAGS[arm]]
    out = ""
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False, errors="replace")
        out = (proc.stdout or "") + (proc.stderr or "")
    m = _SELF.search(out)
    return bool(m) and m.group(1).lower() == "success"


def _grade(task: dict, ws: Path) -> bool:
    """Independent verdict: write the hidden test, run it, then remove it. Never seen by solve."""
    test_path = ws / task["test"]
    test_path.write_text(task["test_src"], encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", task["test"]],
            cwd=str(ws), capture_output=True, text=True, errors="replace", check=False,
        )
        return proc.returncode == 0
    finally:
        with contextlib.suppress(OSError):
            test_path.unlink()


def main() -> None:
    tasks = [t for t in TASKS if not _ONLY or t["id"] in _ONLY]
    print(f"gate A/B · model={_MODEL} · tasks={len(tasks)} · timeout={_TIMEOUT}s/arm", flush=True)
    root = Path(tempfile.mkdtemp(prefix="chimgate-"))
    rows: list[dict] = []
    try:
        for task in tasks:
            rec: dict[str, dict[str, bool]] = {}
            for arm in ("coverage", "gentests"):
                ws = _starter_workspace(task, root)  # paired: identical fresh start per arm
                self_ok = _solve(task, ws, arm)
                real_ok = _grade(task, ws)
                rec[arm] = {"self": self_ok, "real": real_ok}
                fp = self_ok and not real_ok
                print(
                    f"  {task['id']:<16} {arm:<9} self={'PASS' if self_ok else 'fail'} "
                    f"real={'PASS' if real_ok else 'fail'}{'  <FALSE-POSITIVE>' if fp else ''}",
                    flush=True,
                )
            rows.append({"task": task["id"], **rec})
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Paired verdict on the INDEPENDENT grade (McNemar/Wilson), reusing the tested B1 engine.
    from chimera.eval.paired import PairedResult, format_report

    a = b = c = d = 0  # a: both pass · b: cov pass/gen fail · c: cov fail/gen pass · d: both fail
    fp_cov = fp_gen = real_cov = real_gen = 0
    for r in rows:
        cov, gen = r["coverage"], r["gentests"]
        real_cov += cov["real"]
        real_gen += gen["real"]
        fp_cov += cov["self"] and not cov["real"]
        fp_gen += gen["self"] and not gen["real"]
        if cov["real"] and gen["real"]:
            a += 1
        elif cov["real"] and not gen["real"]:
            b += 1
        elif not cov["real"] and gen["real"]:
            c += 1
        else:
            d += 1
    n = len(rows)
    result = PairedResult(
        baseline_name="coverage-grade", treatment_name="gen-tests",
        both_pass=a, baseline_only=b, treatment_only=c, both_fail=d,
    )
    print("\n" + format_report(result), flush=True)
    print(
        f"\nresolve rate — coverage {real_cov}/{n} vs gen-tests {real_gen}/{n}\n"
        f"FALSE POSITIVES (gate said success, hidden test fails) — coverage {fp_cov} vs gen-tests {fp_gen}  "
        "<-- the M18-1 target metric",
        flush=True,
    )
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "gate_ab.json").write_text(
        json.dumps(
            {"model": _MODEL, "n": n, "resolve": {"coverage": real_cov, "gentests": real_gen},
             "false_positives": {"coverage": fp_cov, "gentests": fp_gen},
             "paired": {"a": a, "b": b, "c": c, "d": d}, "rows": rows},
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
