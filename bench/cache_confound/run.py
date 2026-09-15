"""Drive the pre-registered grid: 4 tasks × {SHARED, NONCE} × k replicas at T=0, route pinned.

    python bench/cache_confound/run.py --check                 # instrument check: 3 solves, then stop
    python bench/cache_confound/run.py --grid --pin DeepSeek   # the 32-solve grid
    python bench/cache_confound/run.py --grid --pin DeepSeek --k 4 --ceiling-usd 5

Runs in WSL beside the Harness-Bench checkout (`~/harness-bench`, `~/hb-venv`), exactly as the
factorial did, with one difference that is the whole experiment: `CHIMERA_TEMPERATURE=0` for every
solve, and `CHIMERA_PREFIX_NONCE` set to a fresh UUID for the NONCE condition and unset for SHARED.
The route is pinned with `CHIMERA_PROVIDER_ORDER`, and every receipt's `provider` is checked against
the pin — a solve served by another route is discarded and counted.

What is recorded per solve is what `PREREGISTRATION.md` names and nothing derived: the ordered tool
names, a fingerprint of the files the run changed, the grader's score, the cache tokens the route
reported (None when it reported nothing), the provider, and the cost. The reading is `read.py`'s job.

Needs the OpenRouter key in the environment. The wrapper `run.sh` loads it from the repo's `.env`
without printing it; this file never reads or writes the key.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

HB = Path(os.path.expanduser("~/harness-bench"))
VENV_PY = Path(os.path.expanduser("~/hb-venv/bin/python"))
ROOT = Path(os.path.expanduser("~/cache-confound"))
REPO = Path(__file__).resolve().parents[2]
MODEL = "openrouter/deepseek/deepseek-v3.2"
TASKS = [
    "042-api-schema-migration",
    "086-sql-migration-preflight-rollback",
    "087-cli-parser-bug-tests",
    "092-schema-drift-audit",
]
SOLVE_FLAGS = [
    "--max-attempts", "1", "--max-steps", "120",
    "--no-remember", "--no-collect", "--no-evolve-skills", "--no-manager",
    "--keep-workspace", "--max-usd", "2.0",
    # Amendment 2: without this the planner runs first, at its own hard-coded 0.2, and every worker
    # starts from a different plan — the first request is then not byte-identical and nothing about
    # the route can be read. The factorial's bare arm carried the same flag.
    "--no-plan",
]


def _fingerprint(workspace: Path, fixtures: Path) -> str:
    """sha256 over (relative path, content hash) of every file that differs from the fixture."""
    changed: list[tuple[str, str]] = []
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or ".chimera" in path.parts:
            continue
        rel = path.relative_to(workspace).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        original = fixtures / rel
        if not original.is_file() or hashlib.sha256(original.read_bytes()).hexdigest() != digest:
            changed.append((rel, digest))
    for path in sorted(fixtures.rglob("*")):
        if path.is_file() and not (workspace / path.relative_to(fixtures)).exists():
            changed.append((path.relative_to(fixtures).as_posix(), "DELETED"))
    return hashlib.sha256(json.dumps(changed).encode()).hexdigest()[:16]


def _grade(task: str, workspace: Path) -> float | None:
    grader = HB / "tasks" / task / "oracle_grade.py"
    spec = importlib.util.spec_from_file_location(f"oracle_{task}", grader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"oracle_{task}"] = module
    spec.loader.exec_module(module)
    try:
        result = module.score_workspace(workspace)
    except Exception as exc:  # noqa: BLE001 — a grader that dies is a row, not a crash
        print(f"    grader raised: {type(exc).__name__}: {exc}")
        return None
    value = result.get("outcome_score")
    return float(value) if isinstance(value, (int, float)) else None


def _receipt(home: Path) -> dict:
    log = home / "runs.jsonl"
    if not log.exists():
        return {}
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return {}
    row = json.loads(lines[-1])
    attempts = row.get("attempts") or []
    first = attempts[0] if attempts else {}
    return {
        "tool_names": list(first.get("tool_names") or []),
        "cache_read_tokens": first.get("cache_read_tokens"),
        "prompt_tokens": first.get("prompt_tokens"),
        "completion_tokens": first.get("completion_tokens"),
        "provider": first.get("provider") or "",
        "usd": row.get("usd"),
        "ending": row.get("ending"),
        **_first_request(home),
    }


def _first_request(home: Path) -> dict:
    """Gate M0's evidence, from the artefact: a hash of the task text the worker was given and the
    route's token count for its first request. Two replicas whose first request was byte-identical
    agree on both; the voided cell of Amendment 2 disagreed on both and nobody had looked."""
    log = home / "traces.jsonl"
    if not log.exists():
        return {"task_sha": None, "step1_prompt_tokens": None, "step1_cached_tokens": None}
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return {"task_sha": None, "step1_prompt_tokens": None, "step1_cached_tokens": None}
    trace = json.loads(lines[-1])
    steps = trace.get("steps") or []
    first = steps[0] if steps else {}
    return {
        "task_sha": hashlib.sha256((trace.get("task") or "").encode("utf-8")).hexdigest()[:16],
        "step1_prompt_tokens": first.get("prompt_tokens"),
        "step1_cached_tokens": first.get("cached_tokens"),
    }


def solve(task: str, condition: str, replica: int, pin: str, out: Path) -> dict:
    tag = f"{task}-{condition}-r{replica}"
    ws, home = ROOT / "ws" / tag, ROOT / "homes" / tag
    fixtures = HB / "tasks" / task / "fixtures"
    for d in (ws, home):
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(fixtures, ws)
    home.mkdir(parents=True)

    env = dict(os.environ)
    env.update({
        "CHIMERA_HOME": str(home),
        "CHIMERA_HOST_EXEC": "allow",
        "CHIMERA_TEMPERATURE": "0",
        "CHIMERA_PROVIDER_ORDER": pin,
        "PYTHONPATH": str(REPO),  # THIS checkout's chimera, not the one the venv points at
    })
    nonce = ""
    if condition == "NONCE":
        nonce = uuid.uuid4().hex
        env["CHIMERA_PREFIX_NONCE"] = nonce
    else:
        env.pop("CHIMERA_PREFIX_NONCE", None)

    prompt = (HB / "tasks" / task / "prompt.txt").read_text(encoding="utf-8")
    started = time.time()
    proc = subprocess.run(
        [str(VENV_PY), "-m", "chimera.cli.main", "solve", prompt, "-w", str(ws), "-m", MODEL, *SOLVE_FLAGS],
        cwd=str(REPO), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    seconds = round(time.time() - started, 1)
    receipt = _receipt(home)
    row = {
        "task": task, "condition": condition, "replica": replica, "nonce": nonce, "pin": pin,
        "rc": proc.returncode, "seconds": seconds,
        "outcome_score": _grade(task, ws), "fingerprint": _fingerprint(ws, fixtures),
        **receipt,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    hit = row["cache_read_tokens"]
    print(f"  {tag:<48} rc={proc.returncode} score={row['outcome_score']} "
          f"cache_read={hit} provider={row['provider']!r} usd={row['usd']} fp={row['fingerprint']} "
          f"task_sha={row.get('task_sha')} step1={row.get('step1_cached_tokens')}/{row.get('step1_prompt_tokens')}")
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout or "").splitlines()[-6:])
        print("    stderr tail:", tail.replace("\n", "\n    "))
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="instrument check: S, S, N on one task")
    ap.add_argument("--grid", action="store_true", help="the pre-registered grid")
    ap.add_argument("--pin", default="", help="OpenRouter provider name to pin (required for --grid)")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--ceiling-usd", type=float, default=5.0)
    ap.add_argument("--out", default="")
    # Amendment 1 (see PREREGISTRATION.md): the nonce cannot implement "cache off" on a hosted route,
    # so the grid is replaced by SHARED-only replicas of one task — the T=0 replicate floor with the
    # cache on, which is the only cell the route can actually exhibit.
    ap.add_argument("--extra", default="", help="task:condition:replica,replica,... (amendment 1)")
    args = ap.parse_args()

    ROOT.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else ROOT / ("check.jsonl" if args.check else "grid.jsonl")
    spent = 0.0

    def charge(row: dict) -> None:
        nonlocal spent
        spent += float(row.get("usd") or 0.0)
        if spent > args.ceiling_usd:
            print(f"\n  CEILING: spent US$ {spent:.2f} > {args.ceiling_usd} — stopping.")
            raise SystemExit(2)

    if args.extra:
        task, condition, reps = args.extra.split(":")
        for replica in [int(r) for r in reps.split(",") if r]:
            charge(solve(task, condition, replica, args.pin, out))
        print(f"\n  spent US$ {spent:.3f}; rows in {out}")
        return 0

    if args.check:
        task = TASKS[0]
        print(f"instrument check on {task}: SHARED, SHARED, NONCE (pin={args.pin or 'none'})")
        for condition, replica in (("SHARED", 1), ("SHARED", 2), ("NONCE", 1)):
            charge(solve(task, condition, replica, args.pin, out))
        print(f"\n  spent US$ {spent:.3f}; rows in {out}")
        return 0

    if args.grid:
        if not args.pin:
            print("--grid needs --pin <provider> (read it off the instrument check's receipts)")
            return 1
        print(f"grid: {len(TASKS)} tasks x 2 conditions x k={args.k}, T=0, pin={args.pin}")
        for task in TASKS:
            for replica in range(1, args.k + 1):
                for condition in ("SHARED", "NONCE"):
                    charge(solve(task, condition, replica, args.pin, out))
        print(f"\n  spent US$ {spent:.3f}; rows in {out}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
