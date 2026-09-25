"""Drive the breaker re-measurement (study 24; M6's driver, arm names, tasks, k and paths changed only):
2 arms (legacy / fixed breaker) x 2 executors x 4 tasks x k=10 = 160 solves.

Run in WSL, from a shell that has sourced .env for OPENROUTER_API_KEY:
  ~/hb-venv-brk-fixed/bin/python run_brk.py [--concurrency 6] [--max-usd 10] [--seed 20260926]

Everything else is B4b's: one `run-task` per solve, the home cleared first, resumable, a cell failing
twice frozen as missing, and the >5% rc!=0-or-receiptless halt.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import subprocess
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import product
from pathlib import Path

HB = Path(os.path.expanduser("~/harness-bench"))
HOMES = Path(os.path.expanduser("~/hb-homes"))
LOGS = Path(os.path.expanduser("~/hb-logs"))
PY = os.path.expanduser("~/hb-venv-brk-fixed/bin/python")
DRIVER_LOG = Path(os.path.expanduser("~/hb-driver-brk.jsonl"))

TASKS = ["041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety", "082-compose-config-repair"]
HIDS = [f"brk-{a}-{e}-r{k}" for e in ("strong", "weak") for a in ("legacy", "fixed") for k in range(10)]
"""Forty arms: the legacy and the fixed breaker x two executors x ten replicas. Same wrapper, same flags;
the arm only picks which frozen Chimera is imported."""


FROZEN = Path(os.path.expanduser("~/hb-frozen-brk.txt"))
"""Cells that spent their retry budget (amendment 4). One `<task> <hid>` per line.

A cell is frozen after two failed attempts and is never run again — running a cell until it
succeeds selects for the solves that happen to be fast, and a mean over those is a mean over the
easy half of the distribution. A frozen cell is MISSING, and missing is reported."""


def frozen() -> set[tuple[str, str]]:
    if not FROZEN.is_file():
        return set()
    out = set()
    for line in FROZEN.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out.add((parts[0], parts[1]))
    return out


def freeze(task: str, hid: str) -> None:
    with FROZEN.open("a", encoding="utf-8") as fh:
        fh.write(f"{task} {hid}\n")


def result_exists(hid: str, task: str) -> bool:
    """Done means graded AND priced (amendment 2).

    A provider timeout kills the CLI after the harness has already graded the workspace: the result
    file exists, `runs.jsonl` does not, and a resume that looked only at the result file counted the
    solve as finished forever — its arm silently losing a cost and a step count. Requiring the
    receipt makes a receiptless solve re-run instead."""
    graded = bool(glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json")))
    return graded and solve_usd(hid, task) is not None


def solve_usd(hid: str, task: str) -> float | None:
    """The solve's cost, or None when ANY round could not be priced.

    `or 0.0` on a missing `usd` turns "we do not know what this cost" into "it cost nothing", and
    a receipt that says nothing then counts as a finished, free solve. Measured: the first six Sol
    solves died in ten seconds because `--max-usd` refuses fail-closed on an unpriced model, wrote
    a receipt with `usd: null`, and were recorded as done at US$ 0.00 — a zero that was never a
    measurement (Bee §2z)."""
    p = HOMES / f"{task}-{hid}" / "runs.jsonl"
    if not p.is_file():
        return None
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        return None
    total = 0.0
    for line in lines:
        value = json.loads(line).get("usd")
        if value is None:
            return None
        total += float(value)
    return total


def run_one(hid: str, task: str) -> dict:
    # Clear the home and log so the receipt/log are exactly this solve's (idempotent re-runs).
    shutil.rmtree(HOMES / f"{task}-{hid}", ignore_errors=True)
    (LOGS / f"{task}-{hid}.log").unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    t0 = time.monotonic()
    proc = subprocess.run(
        [PY, "-m", "harnessbench.cli", "run-task", "--task", task, "--harness", hid],
        cwd=str(HB), env=env, capture_output=True, text=True, timeout=3600,
    )
    secs = round(time.monotonic() - t0, 1)
    usd = solve_usd(hid, task)
    outcome = None
    hits = glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    if hits:
        try:
            with open(hits[0], encoding="utf-8") as rf:
                outcome = (json.load(rf).get("oracle_result") or {}).get("outcome_score")
        except Exception:
            outcome = None
    return {
        "task": task, "hid": hid, "rc": proc.returncode, "usd": usd, "outcome": outcome,
        "seconds": secs, "receiptless": usd is None,
        "stderr_tail": (proc.stderr or "")[-300:] if proc.returncode != 0 else "",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--max-usd", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=20260926)
    ap.add_argument("--only-tasks", default="")
    ap.add_argument("--only-arms", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    tasks = args.only_tasks.split(",") if args.only_tasks else TASKS
    hids = args.only_arms.split(",") if args.only_arms else HIDS
    queue = [(h, t) for h, t in product(hids, tasks)]
    random.Random(args.seed).shuffle(queue)
    if args.limit:
        queue = queue[: args.limit]
    # Resume: drop solves already scored.
    cold = frozen()
    pending = [(h, t) for (h, t) in queue if not result_exists(h, t) and (t, h) not in cold]
    if cold:
        print(f"frozen cells skipped (retry budget spent): {len(cold)}")
    print(f"seed={args.seed} queue={len(queue)} already-done={len(queue) - len(pending)} pending={len(pending)} "
          f"concurrency={args.concurrency} max_usd={args.max_usd}", flush=True)

    # Amendment 5: a cell that comes back bad is resubmitted ONCE inside this run, and only a cell
    # that fails TWICE counts toward the stop rule. Every failure so far has been a provider stall
    # hitting the concurrent calls at once (4 within 4s, then 2 within 1s), so the rule as written
    # measured how many solves were in flight during an outage and halted the run every ~22 solves.
    # The retry budget is unchanged — two attempts, then frozen — it is just spent here instead of
    # across relaunches.
    retried: set[tuple[str, str]] = set()

    lock = threading.Lock()
    spent = sum(solve_usd(h, t) or 0.0 for (h, t) in queue if result_exists(h, t))
    done = rc_err = receiptless = 0
    halt = False
    it = iter(pending)
    fh = DRIVER_LOG.open("a", encoding="utf-8")

    def submit_next(ex: ThreadPoolExecutor, futs: set) -> None:
        nonlocal halt
        try:
            h, t = next(it)
        except StopIteration:
            return
        if halt or spent >= args.max_usd:
            halt = True
            return
        futs.add(ex.submit(run_one, h, t))

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs: set = set()
        for _ in range(args.concurrency):
            submit_next(ex, futs)
        while futs:
            got, futs = wait(futs, return_when=FIRST_COMPLETED)
            for f in got:
                r = f.result()
                with lock:
                    done += 1
                    spent += r["usd"] or 0.0
                    bad_now = r["rc"] != 0 or r["receiptless"]
                    if bad_now and (r["task"], r["hid"]) not in retried:
                        # First failure: put it back once, and do not count it yet.
                        retried.add((r["task"], r["hid"]))
                        done -= 1
                        futs.add(ex.submit(run_one, r["hid"], r["task"]))
                        print(f"    resubmitting once: {r['task']} {r['hid']}", flush=True)
                        fh.write(json.dumps(r) + "\n")
                        fh.flush()
                        continue
                    if bad_now:
                        freeze(r["task"], r["hid"])
                        print(f"    frozen after 2 failures: {r['task']} {r['hid']}", flush=True)
                    rc_err += int(r["rc"] != 0)
                    receiptless += int(r["receiptless"])
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    bad = rc_err + receiptless
                    print(f"[{done}/{len(pending)}] {r['task']:<34} {r['hid']:<12} rc={r['rc']} "
                          f"usd={r['usd']} out={r['outcome']} {r['seconds']}s | Σ${spent:.2f} bad={bad}", flush=True)
                    if r["rc"] != 0 and r["stderr_tail"]:
                        print(f"    stderr: {r['stderr_tail']}", flush=True)
                    if spent >= args.max_usd:
                        halt = True
                        print(f"HALT: spend cap US${args.max_usd} reached", flush=True)
                    if done >= 20 and bad / done > 0.05:
                        halt = True
                        print(f"HALT: {bad}/{done} rc!=0-or-receiptless > 5% — investigate apparatus", flush=True)
                if not halt:
                    submit_next(ex, futs)
    fh.close()
    print(f"\nfinished: done={done} spent=US${spent:.2f} rc_err={rc_err} receiptless={receiptless} halted={halt}", flush=True)


if __name__ == "__main__":
    main()
