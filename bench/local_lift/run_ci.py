"""CI quality gauge — run chimera solve on the pytest-graded local tasks and report the pass-rate.

Linux/CI-friendly (unlike run_ab.py, which is Windows-oriented): uses `sys.executable`, the `chimera`
CLI on PATH, and a temp dir per task. For each task it writes the starter files + the strict pytest
file, runs `chimera solve --verify` with the M14 scaffolding, then re-runs pytest INDEPENDENTLY for
the verdict — the pass-rate is the tests' word, never solve's self-report.

This is a *quality gauge* over time (does a change move the number?), not a pass/fail build gate: it
always exits 0 and just prints the pass-rate (and writes it to the GitHub step summary). Configure
via env: BENCH_MODEL (a cheap/free model), BENCH_TIMEOUT (per-solve seconds), BENCH_TASKS (comma ids).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from tasks import TASKS  # noqa: E402

_MODEL = os.environ.get("BENCH_MODEL", "openrouter/deepseek/deepseek-chat-v3.1:free")
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "300"))
_ONLY = {t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()}
_FLAGS = ["--repo-map", "--progress-ledger", "--checklist", "--replan", "--max-attempts", "3",
          "--no-remember", "--no-collect", "--no-evolve-skills"]


def _setup(task: dict, root: Path) -> Path:
    ws = root / task["id"]
    ws.mkdir(parents=True, exist_ok=True)
    for rel, content in task.get("files", {}).items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (ws / task["test"]).write_text(task["test_src"], encoding="utf-8")
    return ws


def _pytest_passes(task: dict, ws: Path) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", task["test"]],
        cwd=str(ws), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    return proc.returncode == 0


def _solve(task: dict, ws: Path) -> int:
    verify = f'"{sys.executable}" -m pytest -q {task["test"]}'
    argv = ["chimera", "solve", task["prompt"], "--workspace", str(ws), "--model", _MODEL,
            "--verify", verify, *_FLAGS]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_TIMEOUT, check=False,
        )
        return proc.returncode
    except subprocess.TimeoutExpired:
        return 124


def main() -> None:
    tasks = [t for t in TASKS if not _ONLY or t["id"] in _ONLY]
    print(f"model={_MODEL} tasks={len(tasks)} timeout={_TIMEOUT}s", flush=True)
    rows: list[dict] = []
    root = Path(tempfile.mkdtemp(prefix="chimbench-"))
    try:
        for task in tasks:
            ws = _setup(task, root)
            t0 = time.time()
            exit_code = _solve(task, ws)
            passed = _pytest_passes(task, ws)
            dt = round(time.time() - t0, 1)
            rows.append({"task": task["id"], "passed": passed, "solve_exit": exit_code, "seconds": dt})
            print(f"  {task['id']:<18} {'PASS' if passed else 'fail'}  ({dt:.0f}s, exit={exit_code})", flush=True)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    n = len(rows)
    npass = sum(1 for r in rows if r["passed"])
    rate = npass / n if n else 0.0
    print(f"\n=== pass-rate: {npass}/{n} = {rate:.0%} (model {_MODEL}) ===", flush=True)

    out = HERE / "results"
    out.mkdir(exist_ok=True)
    (out / "ci.json").write_text(json.dumps({"model": _MODEL, "pass": npass, "total": n, "rows": rows}, indent=2), encoding="utf-8")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [f"### Chimera quality gauge — `{_MODEL}`", "", f"**Pass-rate: {npass}/{n} = {rate:.0%}**", "",
                 "| task | result | time | solve exit |", "|---|---|---|---|"]
        lines += [f"| {r['task']} | {'✅' if r['passed'] else '❌'} | {r['seconds']}s | {r['solve_exit']} |" for r in rows]
        Path(summary).write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
