"""Run the honest weak-model-lift A/B over the local task set (Docker-free).

Two arms, SAME retry budget, the ONLY variable is the M14 scaffolding:
  - baseline : chimera solve with plan + manager + verify-or-revert (3 attempts), no M14 flags
  - chimera  : the same, plus --repo-map --progress-ledger --replan --checklist

For each (task, arm) the runner writes the starter files + the pytest test into a fresh workspace,
runs `chimera solve`, then re-runs pytest INDEPENDENTLY and records that exit code as the verdict —
the test's word, never solve's self-report. Results append to details.jsonl (resumable: a re-run
skips finished cells) and the per-arm pass/fail lists are written for `chimera bench-compare`.

Usage:  uv run --no-sync python bench/local_lift/run_ab.py [--timeout 300]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from tasks import TASKS  # noqa: E402


def _load_dotenv() -> dict[str, str]:
    """Parse the repo .env so the solve subprocess (run from a temp cwd) keeps the provider key."""
    env: dict[str, str] = {}
    dotenv = REPO_ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    return env


_DOTENV = _load_dotenv()

RESULTS = HERE / "results"
DETAILS = RESULTS / "details.jsonl"
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills"]
# The honest product A/B, clearly labelled:
#   baseline = the raw cheap model, ONE shot, no scaffold (no plan / no manager / 1 attempt).
#   chimera  = the full Chimera solve loop: plan + verify-or-revert (3 attempts) + the M14 scaffolding.
# Same model, same tasks; the difference IS "what Chimera's loop adds to the raw model".
ARMS = {
    "baseline": ["--no-plan", "--no-manager", "--max-attempts", "1", *_HYGIENE],
    "chimera": [
        "--no-manager", "--max-attempts", "3",
        "--repo-map", "--progress-ledger", "--replan", "--checklist", *_HYGIENE,
    ],
}
_CHIMERA = shutil.which("chimera") or "chimera"
# The project venv python (has pytest). NOT sys.executable: under `uv run` that is an ephemeral
# interpreter without pytest, which silently breaks both the solve --verify and our own check.
_VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
_PY = str(_VENV_PY) if _VENV_PY.exists() else sys.executable


def _load_done() -> set[tuple[str, str]]:
    done: set[tuple[str, str]] = set()
    if DETAILS.exists():
        for line in DETAILS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                done.add((rec["task"], rec["arm"]))
    return done


def _setup_workspace(task: dict, root: Path) -> Path:
    ws = root / task["id"]
    if ws.exists():
        shutil.rmtree(ws)
    ws.mkdir(parents=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def _run_solve(task: dict, ws: Path, arm_flags: list[str], timeout: int) -> tuple[int, str]:
    verify = f'"{_PY}" -m pytest -q {task["test"]}'
    argv = [
        _CHIMERA, "solve", task["prompt"],
        "--workspace", str(ws),
        "--verify", verify,
        *arm_flags,
    ]
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            check=False, env={**os.environ, **_DOTENV},
        )
        tail = ((proc.stdout or "")[-400:] + (proc.stderr or "")[-400:])
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return 124, "solve timed out"


def _independent_pytest(task: dict, ws: Path) -> bool:
    """The authoritative verdict: run the test ourselves, ignore what solve claimed."""
    proc = subprocess.run(
        [_PY, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=300, help="Per-solve timeout (seconds).")
    parser.add_argument("--sleep", type=float, default=4.0, help="Pause between solves (rate-limit).")
    args = parser.parse_args()

    RESULTS.mkdir(parents=True, exist_ok=True)
    work_root = RESULTS / "workspaces"
    work_root.mkdir(exist_ok=True)
    done = _load_done()
    model = os.environ.get("CHIMERA_DEFAULT_MODEL", "?")
    print(f"model={model}  tasks={len(TASKS)}  arms={list(ARMS)}  (done cells: {len(done)})")

    for task in TASKS:
        for arm, flags in ARMS.items():
            if (task["id"], arm) in done:
                print(f"  skip {task['id']}/{arm} (already done)")
                continue
            ws = _setup_workspace(task, work_root)
            t0 = time.time()
            code, tail = _run_solve(task, ws, flags, args.timeout)
            passed = _independent_pytest(task, ws)
            dt = time.time() - t0
            rec = {
                "task": task["id"], "arm": arm, "passed": passed,
                "solve_exit": code, "seconds": round(dt, 1),
            }
            with DETAILS.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
            mark = "PASS" if passed else "fail"
            print(f"  {task['id']:<18} {arm:<9} {mark}  ({dt:.0f}s, solve_exit={code})")
            time.sleep(args.sleep)

    _write_arm_files()


def _write_arm_files() -> None:
    by_arm: dict[str, dict[str, bool]] = {"baseline": {}, "chimera": {}}
    for line in DETAILS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            by_arm.setdefault(rec["arm"], {})[rec["task"]] = rec["passed"]
    order = [t["id"] for t in TASKS]
    for arm, mapping in by_arm.items():
        trials = [bool(mapping.get(tid, False)) for tid in order]
        (RESULTS / f"{arm}.json").write_text(json.dumps(trials), encoding="utf-8")
        print(f"wrote results/{arm}.json  ({sum(trials)}/{len(trials)} passed)")


if __name__ == "__main__":
    main()
