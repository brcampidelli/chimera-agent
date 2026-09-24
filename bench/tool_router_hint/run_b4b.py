"""Drive B4b (study 22 phase 6; derived from B4's driver, arm names and paths changed only): 2 arms x 3 executors x 23 tasks x k=3 = 414 solves, 6 concurrent.

Run in WSL, from a shell that has sourced .env for OPENROUTER_API_KEY:
  ~/hb-venv-b4b/bin/python run_b4b.py [--concurrency 6] [--max-usd 400] [--seed 20260912]
                                        [--only-tasks 085,087] [--only-arms sys1h-on-strong-r0] [--limit N]

Each solve = one `run-task --task <t> --harness <hid>` (a multi-round task runs its rounds inside
that one call). Before each solve the home is cleared so its runs.jsonl holds exactly this solve's
receipts (summed over rounds by the reader). Resumable: a solve whose result json already exists is
skipped. Stop rules (§7): US$400 spent halts new submissions; if >5% of solves come back rc!=0 or
receiptless (after >=20 done) the run halts for the apparatus to be investigated.
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
PY = os.path.expanduser("~/hb-venv-b4b/bin/python")
DRIVER_LOG = Path(os.path.expanduser("~/hb-driver-b4b.jsonl"))

TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary", "039-repo-architecture-map",
    "040-test-coverage-fill", "041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety",
    "044-ci-config-repair", "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug", "085-flaky-test-root-cause",
    "086-sql-migration-preflight-rollback", "087-cli-parser-bug-tests",
    "089-ab-test-caveat-analysis", "092-schema-drift-audit", "094-metric-definition-migration-diff",
]  # 078 and 088 dropped: they require a public tunnel (cloudflared) absent here — see PREREGISTRATION amendment 2
HIDS = [f"sys1h-{s}-{e}-r{k}" for e in ("strong", "weak", "sol") for s in ("off", "on") for k in (0, 1, 2)]
"""Eighteen arms: router off/on x three executors (strong, weak, sol) x three replicas. The pair (off, on) at
the same executor and replica is the comparison; everything else about them is identical."""


FROZEN = Path(os.path.expanduser("~/hb-frozen-b4b.txt"))
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
    ap.add_argument("--max-usd", type=float, default=25.0)
    ap.add_argument("--seed", type=int, default=20260922)
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
