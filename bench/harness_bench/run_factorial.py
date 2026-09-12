"""Drive the 2^3 x 25 x k=3 = 600 solves (PREREGISTRATION.md §3), 6 concurrent, with the stop rules.

Run in WSL, from a shell that has sourced .env for OPENROUTER_API_KEY:
  ~/hb-venv/bin/python run_factorial.py [--concurrency 6] [--max-usd 400] [--seed 20260912]
                                        [--only-tasks 085,087] [--only-arms arm-000-r0] [--limit N]

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
PY = os.path.expanduser("~/hb-venv/bin/python")
DRIVER_LOG = Path(os.path.expanduser("~/hb-driver.jsonl"))

TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary", "039-repo-architecture-map",
    "040-test-coverage-fill", "041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety",
    "044-ci-config-repair", "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug", "085-flaky-test-root-cause",
    "086-sql-migration-preflight-rollback", "087-cli-parser-bug-tests",
    "089-ab-test-caveat-analysis", "092-schema-drift-audit", "094-metric-definition-migration-diff",
]  # 078 and 088 dropped: they require a public tunnel (cloudflared) absent here — see PREREGISTRATION amendment 2
HIDS = [f"arm-{a}{b}{c}-r{k}" for a in (0, 1) for b in (0, 1) for c in (0, 1) for k in (0, 1, 2)]


def result_exists(hid: str, task: str) -> bool:
    return bool(glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json")))


def solve_usd(hid: str, task: str) -> float | None:
    p = HOMES / f"{task}-{hid}" / "runs.jsonl"
    if not p.is_file():
        return None
    lines = [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    if not lines:
        return None
    return sum(float(json.loads(x).get("usd") or 0.0) for x in lines)


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
            outcome = (json.load(open(hits[0], encoding="utf-8")).get("oracle_result") or {}).get("outcome_score")
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
    ap.add_argument("--max-usd", type=float, default=400.0)
    ap.add_argument("--seed", type=int, default=20260912)
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
    pending = [(h, t) for (h, t) in queue if not result_exists(h, t)]
    print(f"seed={args.seed} queue={len(queue)} already-done={len(queue) - len(pending)} pending={len(pending)} "
          f"concurrency={args.concurrency} max_usd={args.max_usd}", flush=True)

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
                    rc_err += int(r["rc"] != 0)
                    receiptless += int(r["receiptless"])
                    fh.write(json.dumps(r) + "\n"); fh.flush()
                    bad = rc_err + receiptless
                    print(f"[{done}/{len(pending)}] {r['task']:<34} {r['hid']:<12} rc={r['rc']} "
                          f"usd={r['usd']} out={r['outcome']} {r['seconds']}s | Σ${spent:.2f} bad={bad}", flush=True)
                    if r["rc"] != 0 and r["stderr_tail"]:
                        print(f"    stderr: {r['stderr_tail']}", flush=True)
                    if spent >= args.max_usd:
                        halt = True; print(f"HALT: spend cap US${args.max_usd} reached", flush=True)
                    if done >= 20 and bad / done > 0.05:
                        halt = True; print(f"HALT: {bad}/{done} rc!=0-or-receiptless > 5% — investigate apparatus", flush=True)
                if not halt:
                    submit_next(ex, futs)
    fh.close()
    print(f"\nfinished: done={done} spent=US${spent:.2f} rc_err={rc_err} receiptless={receiptless} halted={halt}", flush=True)


if __name__ == "__main__":
    main()
