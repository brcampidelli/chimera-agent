"""Paired weak-model-lift experiment (M15-C1) — baseline vs the full loop from the IDENTICAL state.

`run_ci.py` runs one arm at a time and the two are compared *unpaired* (Newcombe), so the variance
from starting conditions widens the CI — which is exactly why the earlier n=6 run could not reach
significance. This runner uses the M15-B1 machinery instead: for each task it restores the SAME
fresh workspace before each arm (the fork/identical-state discipline), runs the baseline arm and the
full Chimera arm, grades each independently with the task's own pytest, and reports the *paired*
(McNemar/Wilson) verdict alongside the unpaired one. Pairing conditions out the tasks both arms agree
on, so a real lift can clear zero at the small n a disposable test key affords.

Honest by construction: the verdict is the tests' word (pytest re-run independently), never solve's
self-report; both the paired and unpaired numbers are printed so the reader sees what pairing buys.

Configure via env: BENCH_MODEL (a cheap tool-capable model), BENCH_TIMEOUT (per-solve seconds, per
arm), BENCH_TASKS (comma ids). The orchestration core `run_paired` takes injected solve/grade
callables, so it is unit-testable offline; `main()` wires the real `chimera solve` subprocess.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tasks import TASKS  # noqa: E402

# Repo root (…/bench/local_lift → repo root), so the pure-Python imports resolve when run directly.
sys.path.insert(0, str(HERE.parent.parent))

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/meta-llama/llama-3.1-8b-instruct")
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "300"))
_ONLY = {t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()}
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills"]
# The only variable between arms is Chimera's scaffolding (same model, same task, same start state).
_ARM_FLAGS = {
    "baseline": ["--no-plan", "--no-manager", "--max-attempts", "1", *_HYGIENE],
    "chimera": ["--repo-map", "--progress-ledger", "--checklist", "--replan", "--max-attempts", "3", *_HYGIENE],
}

# Injected callables (typed for the testable core):
#   SolveFn(task, workspace, arm) -> None   — run one arm in `workspace`
#   GradeFn(task, workspace) -> bool         — the independent pass/fail verdict
SolveFn = Callable[[dict, Path, str], None]
GradeFn = Callable[[dict, Path], bool]


def _fresh_workspace(task: dict, root: Path) -> Path:
    """Restore the task's starter files + test into a clean workspace — the per-arm 'fork'."""
    ws = root / task["id"]
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def run_paired(tasks: list[dict], *, solve: SolveFn, grade: GradeFn, root: Path):  # type: ignore[no-untyped-def]
    """Run both arms from the identical restored state per task; return the paired result + rows.

    Uses :func:`chimera.eval.paired.run_paired_experiment` so the discipline (restore before each
    arm) and the statistic (McNemar/Wilson) are the tested B1 code, not re-implemented here.
    """
    from chimera.eval.paired import run_paired_experiment

    rows: list[dict] = []

    def restore(task: dict) -> None:
        _fresh_workspace(task, root)

    def _arm(task: dict, arm: str) -> bool:
        ws = root / task["id"]
        solve(task, ws, arm)
        return grade(task, ws)

    def baseline(task: dict) -> bool:
        ok = _arm(task, "baseline")
        rows.append({"task": task["id"], "arm": "baseline", "passed": ok})
        return ok

    def treatment(task: dict) -> bool:
        ok = _arm(task, "chimera")
        rows.append({"task": task["id"], "arm": "chimera", "passed": ok})
        return ok

    result = run_paired_experiment(
        tasks, restore=restore, baseline=baseline, treatment=treatment,
        baseline_name="raw-model", treatment_name="chimera",
    )
    return result, rows


def _real_solve(task: dict, ws: Path, arm: str) -> None:
    import contextlib

    verify = f'"{sys.executable}" -m pytest -q {task["test"]}'
    argv = ["chimera", "solve", task["prompt"], "--workspace", str(ws), "--model", _MODEL,
            "--verify", verify, *_ARM_FLAGS[arm]]
    # A timed-out arm simply fails its pytest below — an honest failure, not a crash.
    with contextlib.suppress(subprocess.TimeoutExpired):
        subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False)


def _real_grade(task: dict, ws: Path) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True, errors="replace", check=False,
    )
    return proc.returncode == 0


def main() -> None:
    tasks = [t for t in TASKS if not _ONLY or t["id"] in _ONLY]
    print(f"paired experiment · model={_MODEL} · tasks={len(tasks)} · timeout={_TIMEOUT}s/arm", flush=True)
    root = Path(tempfile.mkdtemp(prefix="chimpaired-"))
    try:
        result, _rows = run_paired(tasks, solve=_real_solve, grade=_real_grade, root=root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    # Reconstruct the aligned per-arm lists from the paired counts is lossy; instead re-derive from
    # the result object which already holds a/b/c/d. Print the paired verdict (the point of C1)…
    from chimera.eval.paired import format_report as format_paired

    print("\n" + format_paired(result), flush=True)

    # …and the unpaired Newcombe verdict on the same marginals, so the tightening is visible.
    print(
        f"\n[unpaired marginals] raw-model {result.baseline_rate:.0%} vs chimera "
        f"{result.treatment_rate:.0%} — paired is the tighter, honest read here.",
        flush=True,
    )

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "paired.json").write_text(json.dumps(result.summary(), indent=2), encoding="utf-8")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        Path(summary).write_text(
            f"### Paired weak-model lift on `{_MODEL}`\n\n```\n{format_paired(result)}\n```\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
