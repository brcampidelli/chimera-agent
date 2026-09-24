"""M7 step 2 — the element list today against viewport-first, on browsing tasks. See PREREGISTRATION.md.

    python -m bench.browser_viewport_tasks.run --dry-run            # US$ 0: every apparatus check
    python bench/_with_env.py bench/browser_viewport_tasks/run.py --run [--max-usd 8] [--concurrency 4]

The paid run needs an OpenRouter key in the environment: `_with_env.py` loads it from the `.env` of the
checkout it lives in (a worktree has none of its own — use the main checkout's `_with_env.py` with this
script's path, or export the key). Each solve then drops every other CHIMERA_* variable, so the agent
runs on the code's defaults, not on this machine's `.env`.

One subprocess per solve (`solve_one.py`), 24 tasks x 2 arms x k replicas, shuffled with a fixed seed so
the arms interleave in time. Resumable: a solve whose record exists without an error is not run again.

Stop rules (registered):
* **The cap is hard.** A solve is submitted only while the money already spent, plus every solve in
  flight reserved at its worst case, plus this one, stays within `--max-usd`. Spent is each record's
  `cap_usd` — its tokens at the dearest rate the catalogue has seen for the model, never less than
  the receipt — and the worst case of one solve is its own ceiling (`PER_SOLVE_USD`, enforced at the
  receipt's rate) times how much dearer that rate can be (see `solve_one.worst_price`).
* **An errored solve is resubmitted once**; a cell that errors twice is frozen as MISSING and never
  run again (running a cell until it succeeds selects for the easy draws).
* **More than 5% of finished cells missing, after at least 20, halts the run for the apparatus.**
An error is the apparatus or the provider breaking (an exception, no receipt, no price) — a wrong
answer is an outcome, never an error.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.browser_viewport_tasks.harness import ARMS  # noqa: E402
from bench.browser_viewport_tasks.solve_one import MODEL, PER_SOLVE_USD, worst_price  # noqa: E402
from bench.browser_viewport_tasks.tasks import TASKS  # noqa: E402

HERE = Path(__file__).resolve().parent
#: Step 2 wrote to `results/`; a later registered step points M7_RESULTS at its own folder (step 3:
#: `results-step3`), so the resume logic never mistakes step 2's records for its own.
RESULTS = HERE / os.environ.get("M7_RESULTS", "results")
SOLVES = RESULTS / "solves"
FROZEN = RESULTS / "frozen.txt"
DRIVER_LOG = RESULTS / "driver.jsonl"
K = 5
SEED = 20260924


def cell_path(task: str, arm: str, replica: int) -> Path:
    return SOLVES / f"{task}__{arm}__r{replica}.json"


def load(path: Path) -> dict[str, Any] | None:
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def finished(path: Path) -> bool:
    """Done means answered AND priced: a record with an error, or without a usd, runs again."""
    rec = load(path)
    return rec is not None and not rec.get("error") and rec.get("usd") is not None


def charge(rec: dict[str, Any], reserve: float) -> float:
    """What the cap counts for one attempt: its `cap_usd`, or — when the record has none (the process
    died before writing one) — the reserve, because an unknown spend is never counted as zero."""
    value = rec.get("cap_usd")
    return float(value) if value is not None else reserve


def spent_so_far() -> float:
    """The cap's spend of every earlier attempt, read from the driver log (one line per attempt,
    retries included), which is what a relaunch must count before it submits anything."""
    if not DRIVER_LOG.is_file():
        return 0.0
    reserve = PER_SOLVE_USD * worst_price(MODEL)[2]
    total = 0.0
    for line in DRIVER_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            total += charge(json.loads(line), reserve)
    return total


def frozen() -> set[str]:
    if not FROZEN.is_file():
        return set()
    return {line.strip() for line in FROZEN.read_text(encoding="utf-8").splitlines() if line.strip()}


def commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def run_cell(task: str, arm: str, replica: int, head: str) -> dict[str, Any]:
    out = cell_path(task, arm, replica)
    out.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["M7B_COMMIT"] = head
    env.pop("CHIMERA_BROWSER_VIEWPORT_FIRST", None)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "bench.browser_viewport_tasks.solve_one", "--task", task, "--arm", arm,
             "--replica", str(replica), "--out", str(out)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=1200,
        )
        rc, tail = proc.returncode, (proc.stderr or "")[-400:]
    except subprocess.TimeoutExpired:
        rc, tail = -9, "timeout after 1200 s"
    rec = load(out) or {"task": task, "arm": arm, "replica": replica, "error": f"no record (rc={rc})"}
    rec["rc"] = rc
    if rc != 0 and not rec.get("error"):
        rec["error"] = f"rc={rc}"
    if rec.get("error"):
        rec["stderr_tail"] = tail
    return rec


def paid_run(args: argparse.Namespace) -> int:
    # Presence only; the value is never read here.
    if not (os.environ.get("OPENROUTER_API_KEY") or os.environ.get("CHIMERA_OPENROUTER_KEYS")):
        print("refusing: no OpenRouter key in the environment (export it, or launch through the main "
              "checkout's bench/_with_env.py — this worktree has no .env of its own)")
        return 2
    cells = [(t.id, arm, r) for t in TASKS for arm in ARMS for r in range(args.k)]
    random.Random(args.seed).shuffle(cells)
    cold = frozen()
    done_before = [c for c in cells if finished(cell_path(*c))]
    pending = [c for c in cells if c not in done_before and "__".join(map(str, c)) not in cold]
    reserve = PER_SOLVE_USD * worst_price(MODEL)[2]
    # Every record on disk cost money, finished or not; `driver.jsonl` also holds the first attempts
    # a retry overwrote, so the spend of earlier launches is read from there.
    spent = spent_so_far()
    print(f"cells={len(cells)} done={len(done_before)} frozen={len(cold)} pending={len(pending)} "
          f"spent=US${spent:.4f} (charged at the dearest seen rate) cap=US${args.max_usd} "
          f"reserve/solve=US${reserve:.4f} concurrency={args.concurrency}", flush=True)

    head = commit()
    SOLVES.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    # A cell whose record on disk already carries an error spent its first attempt in an earlier
    # launch: the budget is two attempts per cell, not two per relaunch.
    retried: set[tuple[str, str, int]] = {c for c in pending if (load(cell_path(*c)) or {}).get("error")}
    in_flight: dict[Future[dict[str, Any]], tuple[str, str, int]] = {}
    queue = list(pending)
    finished_cells = missing = 0
    halt = ""

    def can_submit() -> bool:
        return spent + (len(in_flight) + 1) * reserve <= args.max_usd

    def submit(ex: ThreadPoolExecutor, cell: tuple[str, str, int]) -> None:
        in_flight[ex.submit(run_cell, *cell, head)] = cell

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex, DRIVER_LOG.open("a", encoding="utf-8") as log:
        while queue and len(in_flight) < args.concurrency and can_submit():
            submit(ex, queue.pop(0))
        if queue and not in_flight:
            halt = f"spend cap: US${spent:.4f} spent, the next solve could pass US${args.max_usd}"
        while in_flight:
            got, _ = wait(list(in_flight), return_when=FIRST_COMPLETED)
            for fut in got:
                cell = in_flight.pop(fut)
                rec = fut.result()
                with lock:
                    spent += charge(rec, reserve)
                    log.write(json.dumps({k: rec.get(k) for k in ("task", "arm", "replica", "success", "usd",
                                                                   "cap_usd", "steps", "prompt_tokens", "error",
                                                                   "rc")}) + "\n")
                    log.flush()
                    if rec.get("error") and cell not in retried:
                        retried.add(cell)
                        queue.insert(0, cell)
                        print(f"    resubmitting once: {cell} — {rec['error'][:120]}", flush=True)
                        continue
                    finished_cells += 1
                    if rec.get("error"):
                        missing += 1
                        with FROZEN.open("a", encoding="utf-8") as fh:
                            fh.write("__".join(map(str, cell)) + "\n")
                        print(f"    frozen as missing after two errors: {cell}", flush=True)
                    print(f"[{finished_cells}/{len(pending)}] {cell[0]:<3} {cell[1]:<8} r{cell[2]} "
                          f"success={rec.get('success')} steps={rec.get('steps')} "
                          f"prompt={rec.get('prompt_tokens')} usd={rec.get('usd')} | Σ US${spent:.4f} "
                          f"missing={missing}", flush=True)
                    if finished_cells >= 20 and missing / finished_cells > 0.05:
                        halt = f"{missing}/{finished_cells} cells missing > 5% — investigate the apparatus"
            while not halt and queue and len(in_flight) < args.concurrency:
                if not can_submit():
                    halt = halt or f"spend cap: US${spent:.4f} spent, next solve could pass US${args.max_usd}"
                    break
                submit(ex, queue.pop(0))
    if halt:
        print(f"HALT: {halt}", flush=True)
    print(f"finished={finished_cells} missing={missing} spent=US${spent:.4f} left={len(queue)}", flush=True)
    return 1 if halt else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="US$ 0: apparatus checks, no model call")
    mode.add_argument("--run", action="store_true", help="the paid run (needs OPENROUTER_API_KEY)")
    ap.add_argument("--max-usd", type=float, default=8.0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--k", type=int, default=K)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    if args.run:
        return paid_run(args)

    # The stand-in solve reads settings like the real one: give it a home of its own, not this machine's.
    os.environ["CHIMERA_HOME"] = tempfile.mkdtemp(prefix="m7b-dry-")
    from bench.browser_viewport_tasks.dryrun import dry_run

    report, failures = dry_run(args.k, MODEL, args.max_usd)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "dry_run.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print_dry_run(report)
    return 1 if failures else 0


def print_dry_run(report: dict[str, Any]) -> None:
    s = report["suite"]
    print(f"suite: {s['tasks']} tasks — {s['in_view']} in view, {s['below']} below the fold; "
          f"hurt-prone {s['hurt_prone']}; checkers {s['checkers']}")
    print(f"site: {report['site']['pages']} pages, manifest matches: {report['site']['manifest_matches']}")
    pages = report["pages"]
    print(f"pages: median {pages['elements_median']} elements, median off-screen share "
          f"{pages['offscreen_share_median']:.0%}")
    for site, row in pages["pages"].items():
        print(f"  {site:<6} {row['elements']:>5} elements  off-screen {row['offscreen']:>5}  "
              f"list chars today {row['chars_today']:>6}  viewport-first {row['chars_viewport']:>5}")
    print("placement (measured) and scripted reachability:")
    for t in TASKS:
        p = report["placement"].get(t.id, {})
        line = f"  {t.id:<3} {t.stratum:<7} {'hurt' if t.hurt_prone else '    '} target={p.get('where', '?'):<6}"
        for arm in ARMS:
            row = report["scripts"][arm][t.id]
            line += f"  {arm}: {'OK ' if row['accepted'] else 'BAD'} {len(row['calls']):>2} calls {sum(row['chars']):>6} ch"
        print(line)
    for arm, row in report["solve_path"].items():
        print(f"solve path, stand-in backend [{arm}]: success={row['success']} answer={row['answer']} "
              f"steps={row['steps']} usd={row['usd']} actions={row['browser_actions']} "
              f"scroll offered={row['offers_scroll']} error={row['error']!r}")
    proj = report["projection"]
    print(f"projection ({proj['model']} at {proj['price_in_per_m']}/{proj['price_out_per_m']} per M, "
          f"x{proj['inflate']} for detours, k={proj['k']}, {proj['solves']} solves):")
    for arm, row in proj["per_arm"].items():
        print(f"  {arm:<8} scripted prompt tokens mean {row['scripted_prompt_tokens_mean']:>7,} "
              f"max {row['scripted_prompt_tokens_max']:>7,}  projected US$ {row['projected_usd_per_solve']:.4f}/solve")
    print(f"  projected total US$ {proj['projected_total_usd']:.2f}")
    if report["failures"]:
        print("FAILURES:")
        for f in report["failures"]:
            print(f"  - {f}")
    else:
        print("dry-run: every check passed")


if __name__ == "__main__":
    raise SystemExit(main())
