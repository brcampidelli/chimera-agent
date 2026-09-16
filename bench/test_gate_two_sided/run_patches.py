"""Step 1 of `PREREGISTRATION.md`: a labelled patch history for the 30 `fix_*` tasks.

    python bench/test_gate_two_sided/run_patches.py [--k 2] [--model …] [--out results/patches.jsonl]

`chimera solve` on the buggy base with NO gate and NO hidden test in the workspace — the round-0
patch production would have before any verifier looks at it. The hidden test is written in
afterwards and run once; that verdict is the label. The post-patch content of every file the
solve changed is stored, so every later reading is from the artefact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bench" / "local_lift"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from tasks import TASKS  # noqa: E402  (bench/local_lift)

HERE = Path(__file__).resolve().parent
SOLVE_FLAGS = [
    "--max-attempts", "1", "--max-steps", "40",
    "--no-remember", "--no-collect", "--no-evolve-skills", "--no-manager", "--no-plan",
    "--keep-workspace", "--max-usd", "0.10",
]


def fix_tasks() -> list[dict[str, Any]]:
    return [t for t in TASKS if str(t["id"]).startswith("fix_")]


def materialise(task: dict[str, Any], root: Path) -> None:
    for rel, content in task["files"].items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def grade(task: dict[str, Any], ws: Path) -> tuple[bool, str]:
    """The hidden test, written in after the solve and removed after the run."""
    test_path = ws / task["test"]
    test_path.write_text(task["test_src"], encoding="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", task["test"]],
            cwd=str(ws), capture_output=True, text=True, errors="replace", check=False, timeout=120,
        )
        return proc.returncode == 0, (proc.stdout or "")[-600:]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    finally:
        test_path.unlink(missing_ok=True)
        shutil.rmtree(ws / ".pytest_cache", ignore_errors=True)


def changed_files(task: dict[str, Any], ws: Path) -> dict[str, str | None]:
    """{relative path: post-patch content} for every task file the solve changed or removed, plus
    any new .py file it added. None marks a deletion."""
    out: dict[str, str | None] = {}
    for rel, before in task["files"].items():
        path = ws / rel
        if not path.is_file():
            out[rel] = None
        else:
            after = path.read_text(encoding="utf-8", errors="replace")
            if after != before:
                out[rel] = after
    for path in ws.rglob("*.py"):
        rel = path.relative_to(ws).as_posix()
        if rel not in task["files"] and "__pycache__" not in rel and not rel.startswith(".chimera"):
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
    return out


def solve_once(task: dict[str, Any], replica: int, model: str, root: Path) -> dict[str, Any]:
    ws = root / f"{task['id']}-r{replica}"
    home = root / "homes" / f"{task['id']}-r{replica}"
    shutil.rmtree(ws, ignore_errors=True)
    shutil.rmtree(home, ignore_errors=True)
    ws.mkdir(parents=True)
    home.mkdir(parents=True)
    materialise(task, ws)
    env = dict(os.environ)
    env.update({"CHIMERA_HOME": str(home), "PYTHONPATH": str(REPO)})
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "-m", "chimera.cli.main", "solve", task["prompt"], "-w", str(ws), "-m", model, *SOLVE_FLAGS],
        cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    seconds = round(time.time() - started, 1)
    files = changed_files(task, ws)
    unpatched = not files
    correct, tail = (False, "") if unpatched else grade(task, ws)
    usd = None
    runs = home / "runs.jsonl"
    if runs.is_file():
        lines = [ln for ln in runs.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            usd = json.loads(lines[-1]).get("usd")
    return {
        "task_id": task["id"], "replica": replica, "model": model, "rc": proc.returncode,
        "seconds": seconds, "usd": usd, "unpatched": unpatched,
        "label": "unpatched" if unpatched else ("correct" if correct else "incorrect"),
        "files": files, "verify_file": task["verify"],
        "patch_sha": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:16],
        "grade_tail": tail,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=2)
    ap.add_argument("--model", default="openrouter/deepseek/deepseek-v4-flash-0731")
    ap.add_argument("--out", type=Path, default=HERE / "results" / "patches.jsonl")
    ap.add_argument("--only", default="", help="comma-separated task ids (default: all fix_* tasks)")
    args = ap.parse_args()
    tasks = fix_tasks()
    if args.only:
        wanted = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in wanted]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Resume: rows already on disk are kept, not re-solved. The first launch was killed by the
    # shell's timeout half-way; append-only output plus this skip is what makes a kill cheap.
    done: set[tuple[str, int]] = set()
    if args.out.is_file():
        for line in args.out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                done.add((str(row["task_id"]), int(row["replica"])))
    root = Path(tempfile.mkdtemp(prefix="testgate-patches-"))
    spent = 0.0
    with args.out.open("a", encoding="utf-8") as handle:
        for task in tasks:
            for replica in range(1, args.k + 1):
                if (str(task["id"]), replica) in done:
                    continue
                row = solve_once(task, replica, args.model, root)
                spent += float(row["usd"] or 0.0)
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                print(f"  {task['id']:<22} r{replica} {row['label']:<10} rc={row['rc']} "
                      f"files={len(row['files'])} usd={row['usd']} {row['seconds']}s", flush=True)
    shutil.rmtree(root, ignore_errors=True)
    print(f"\n  spent US$ {spent:.3f}; rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
