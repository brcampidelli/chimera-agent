"""Drive the M6 fork (study 24; derived from M6's driver): run escalating solves with a snapshot at the trip
until each executor has its registered number of pairs, or the cap is reached.

Run in WSL, from a login shell that has loaded OPENROUTER_API_KEY (see the chunk script):
  ~/hb-venv-m6f/bin/python run_m6f.py [--concurrency 6] [--max-usd 20] [--pairs 20] [--seed 20260925]

A pair is a solve whose breaker tripped: its snapshot exists. Solves that never trip are the same in
both arms and form no pair; they are run, paid and counted, never analysed as pairs. Everything else
is M6's apparatus: the home, log AND snapshot cleared before a run, resumable, a cell failing twice
frozen as missing, and the >5% rc!=0-or-receiptless halt.
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
SNAPS = Path(os.path.expanduser("~/hb-snapshots"))
PY = os.path.expanduser("~/hb-venv-m6f/bin/python")
DRIVER_LOG = Path(os.path.expanduser("~/hb-driver-m6f.jsonl"))
FROZEN = Path(os.path.expanduser("~/hb-frozen-m6f.txt"))

TASKS = ["041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety", "082-compose-config-repair"]
REPLICAS = {"strong": 40, "weak": 120}


def hid_of(executor: str, k: int) -> str:
    return f"m6f-{executor}-r{k}"


def executor_of(hid: str) -> str:
    return hid.split("-")[1]


def frozen() -> set[tuple[str, str]]:
    if not FROZEN.is_file():
        return set()
    return {tuple(line.split()) for line in FROZEN.read_text(encoding="utf-8").splitlines() if len(line.split()) == 2}  # type: ignore[misc]


def freeze(task: str, hid: str) -> None:
    with FROZEN.open("a", encoding="utf-8") as fh:
        fh.write(f"{task} {hid}\n")


def solve_usd(hid: str, task: str) -> float | None:
    """The solve's cost, or None when any round could not be priced (M6's rule: unknown is never 0)."""
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


def result_exists(hid: str, task: str) -> bool:
    graded = bool(glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json")))
    return graded and solve_usd(hid, task) is not None


def snapshot_of(hid: str, task: str) -> Path:
    return SNAPS / f"{task}-{hid}"


def clear_snapshot(hid: str, task: str) -> None:
    """Remove a stale snapshot before a (re)run — guarded: only a direct child of ~/hb-snapshots."""
    target = snapshot_of(hid, task).resolve()
    if target.parent != SNAPS.resolve() or not target.name.startswith(task):
        raise SystemExit(f"refusing to remove {target}: not a snapshot of this bench")
    shutil.rmtree(target, ignore_errors=True)


def run_one(hid: str, task: str) -> dict:
    shutil.rmtree(HOMES / f"{task}-{hid}", ignore_errors=True)
    (LOGS / f"{task}-{hid}.log").unlink(missing_ok=True)
    clear_snapshot(hid, task)
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
        "tripped": snapshot_of(hid, task).is_dir(), "seconds": secs, "receiptless": usd is None,
        "stderr_tail": (proc.stderr or "")[-300:] if proc.returncode != 0 else "",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--max-usd", type=float, default=20.0)
    ap.add_argument("--pairs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--only-executor", default="")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    executors = [args.only_executor] if args.only_executor else ["strong", "weak"]
    rng = random.Random(args.seed)
    queues: dict[str, list[tuple[str, str]]] = {}
    for ex in executors:
        cells = [(hid_of(ex, k), t) for k, t in product(range(REPLICAS[ex]), TASKS)]
        rng.shuffle(cells)
        queues[ex] = cells[: args.limit] if args.limit else cells
    cold = frozen()
    done_cells = {ex: [(h, t) for (h, t) in q if result_exists(h, t)] for ex, q in queues.items()}
    pairs = {ex: sum(snapshot_of(h, t).is_dir() for (h, t) in done_cells[ex]) for ex in executors}
    pending = {ex: [(h, t) for (h, t) in q if not result_exists(h, t) and (t, h) not in cold] for ex, q in queues.items()}
    spent = sum(solve_usd(h, t) or 0.0 for ex in executors for (h, t) in done_cells[ex])
    print(f"seed={args.seed} pairs so far={pairs} target={args.pairs} spent=US${spent:.2f} "
          f"pending={ {ex: len(v) for ex, v in pending.items()} } frozen={len(cold)}", flush=True)

    lock = threading.Lock()
    retried: set[tuple[str, str]] = set()
    done = rc_err = receiptless = 0
    halt = False
    iters = {ex: iter(v) for ex, v in pending.items()}
    order = list(executors)
    turn = 0
    fh = DRIVER_LOG.open("a", encoding="utf-8")

    def submit_next(pool: ThreadPoolExecutor, futs: set) -> None:
        """Alternate executors; skip one that has its pairs or ran out of replicas."""
        nonlocal halt, turn
        if halt or spent >= args.max_usd:
            halt = True
            return
        for _ in range(len(order)):
            ex = order[turn % len(order)]
            turn += 1
            if pairs[ex] >= args.pairs:
                continue
            nxt = next(iters[ex], None)
            if nxt is not None:
                futs.add(pool.submit(run_one, *nxt))
                return

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs: set = set()
        for _ in range(args.concurrency):
            submit_next(pool, futs)
        while futs:
            got, futs = wait(futs, return_when=FIRST_COMPLETED)
            for f in got:
                r = f.result()
                with lock:
                    done += 1
                    spent += r["usd"] or 0.0
                    bad_now = r["rc"] != 0 or r["receiptless"]
                    if bad_now and (r["task"], r["hid"]) not in retried:
                        retried.add((r["task"], r["hid"]))
                        done -= 1
                        futs.add(pool.submit(run_one, r["hid"], r["task"]))
                        print(f"    resubmitting once: {r['task']} {r['hid']}", flush=True)
                        fh.write(json.dumps(r) + "\n")
                        fh.flush()
                        continue
                    if bad_now:
                        freeze(r["task"], r["hid"])
                        print(f"    frozen after 2 failures: {r['task']} {r['hid']}", flush=True)
                    else:
                        pairs[executor_of(r["hid"])] += int(r["tripped"])
                    rc_err += int(r["rc"] != 0)
                    receiptless += int(r["receiptless"])
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    bad = rc_err + receiptless
                    print(f"[{done}] {r['task']:<26} {r['hid']:<16} rc={r['rc']} usd={r['usd']} out={r['outcome']} "
                          f"trip={int(r['tripped'])} {r['seconds']}s | pairs={pairs} Σ${spent:.2f} bad={bad}", flush=True)
                    if spent >= args.max_usd:
                        halt = True
                        print(f"HALT: spend cap US${args.max_usd} reached", flush=True)
                    if done >= 20 and bad / done > 0.05:
                        halt = True
                        print(f"HALT: {bad}/{done} rc!=0-or-receiptless > 5% — investigate apparatus", flush=True)
                if not halt:
                    submit_next(pool, futs)
    fh.close()
    print(f"\nfinished: done={done} pairs={pairs} spent=US${spent:.2f} rc_err={rc_err} "
          f"receiptless={receiptless} halted={halt}", flush=True)


if __name__ == "__main__":
    main()
