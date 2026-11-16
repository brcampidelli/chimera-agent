"""Drive the planner A/B (bench/planner_ab/PREREGISTRATION.md) for one model, in WSL.

  ~/hb-venv/bin/python run_ab.py --tag glm --phase pilot            # first 12 triples, kept in the data
  ~/hb-venv/bin/python run_ab.py --tag glm --phase rest --model-cap 2.50

Arms: `plan` = chimera solve as is, `noplan` = `--no-plan`. Harness id `p101-<tag>-<arm>-r<k>`.
Each solve is one `harnessbench run-task` (a multi-round task runs its rounds inside it). Resumable:
a solve whose result json exists is skipped. Spend is read from the receipts under ~/p101-homes, never
from the provider's key counter (shared with other items). Stop rules are PREREGISTRATION.md section 6.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _legacy_defaults import pin_legacy_defaults  # noqa: E402

HB = Path(os.path.expanduser("~/harness-bench"))
HOMES = Path(os.path.expanduser("~/p101-homes"))
LOGS = Path(os.path.expanduser("~/p101-logs"))
PY = os.path.expanduser("~/hb-venv/bin/python")
WRAPPER = str(Path.home() / "p101-solve.sh")
DRIVER_LOG = Path(os.path.expanduser("~/p101-driver.jsonl"))
CONFIG = HB / "config" / "harness.yaml"

MODELS = {
    "glm": "openrouter/z-ai/glm-5.3-flash",
    "qwen": "openrouter/qwen/qwen3-coder-next",
}
ARMS = {"plan": [], "noplan": ["--no-plan"]}
REPLICAS = (0, 1, 2)
SEED = 20261116
HARD_STOP_USD = 4.00
SAFETY = 1.25  # Amendment 1: spend = token-priced cost x 1.25, no cache discount taken
#: USD per million tokens (in, out), OpenRouter list price on the day of registration (2026-11-16).
PRICE = {"glm": (0.0399, 0.0784), "qwen": (0.12, 0.80)}
PILOT_N = 12
TASKS = [
    "011-code-debug", "016-code-repair-pytest", "022-local-rest-api-summary", "039-repo-architecture-map",
    "040-test-coverage-fill", "041-frontend-state-bug", "042-api-schema-migration", "043-db-migration-safety",
    "044-ci-config-repair", "045-dependency-upgrade-compat", "047-code-review-risk-report", "051-sql-query-report",
    "064-service-dependency-triage", "080-schema-roundtrip-conversion",
    "082-compose-config-repair", "083-monorepo-interface-repair", "084-js-state-type-bug", "085-flaky-test-root-cause",
    "086-sql-migration-preflight-rollback", "087-cli-parser-bug-tests",
    "089-ab-test-caveat-analysis", "092-schema-drift-audit", "094-metric-definition-migration-diff",
]


def hid_of(tag: str, arm: str, k: int) -> str:
    return f"p101-{tag}-{arm}-r{k}"


def write_config(tag: str) -> None:
    """Add this tag's 6 harness entries (idempotent); every other entry is left untouched."""
    text = CONFIG.read_text(encoding="utf-8")
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError:
        import yaml

        cfg = yaml.safe_load(text) or {}
    models = cfg.setdefault("models", {})
    for arm, k in product(ARMS, REPLICAS):
        h = hid_of(tag, arm, k)
        models[h] = {
            "adapter": "generic_cli", "command": WRAPPER, "timeout_sec": 2400, "session_prefix": f"hb-{h}",
            "args": ["{task_id}", "{prompt_file}", "{workspace}", h, MODELS[tag], *ARMS[arm]],
        }
    backup = CONFIG.with_suffix(CONFIG.suffix + ".bak-p101")
    if not backup.exists():
        shutil.copy2(CONFIG, backup)
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def result_path(hid: str, task: str) -> str | None:
    hits = glob.glob(str(HB / "data_try6" / "results" / hid / "*" / f"{task}.json"))
    return hits[0] if hits else None


def receipts(hid: str, task: str) -> list[dict]:
    p = HOMES / f"{task}-{hid}" / "runs.jsonl"
    if not p.is_file():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def solve_usd(hid: str, task: str) -> float | None:
    """The chimera meter's number (what the factorial called usd). Not used for the ceiling."""
    rs = receipts(hid, task)
    return sum(float(r.get("usd") or 0.0) for r in rs) if rs else None


def solve_real_usd(tag: str, hid: str, task: str) -> float | None:
    rs = receipts(hid, task)
    return tokens_usd(tag, rs) if rs else None


def tokens_usd(tag: str, rs: list[dict]) -> float:
    """Token-priced cost of a solve's receipts at list price. The chimera meter does not know these
    models and falls back to ~US$1.5/M input (measured: 35-40x too high for glm-5.3-flash), so the
    meter's `usd` is recorded but never used for the ceiling (PREREGISTRATION Amendment 1)."""
    pin, pout = PRICE[tag]
    return sum(
        (a.get("prompt_tokens") or 0) * pin / 1e6 + (a.get("completion_tokens") or 0) * pout / 1e6
        for r in rs for a in (r.get("attempts") or [])
    )


def total_spent() -> float:
    """Every receipt under ~/p101-homes, all tags, token-priced x SAFETY: the item's ceiling is
    across everything."""
    total = 0.0
    for p in HOMES.glob("*/runs.jsonl"):
        tag = p.parent.name.split("-p101-")[1].split("-")[0]
        rs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        total += tokens_usd(tag, rs)
    return total * SAFETY


def run_one(hid: str, task: str, tag: str, arm: str, k: int) -> dict:
    shutil.rmtree(HOMES / f"{task}-{hid}", ignore_errors=True)
    (LOGS / f"{task}-{hid}.log").unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    t0 = time.monotonic()
    proc = subprocess.run([PY, "-m", "harnessbench.cli", "run-task", "--task", task, "--harness", hid],
                          cwd=str(HB), env=env, capture_output=True, text=True, timeout=3600)
    secs = round(time.monotonic() - t0, 1)
    outcome = None
    rp = result_path(hid, task)
    if rp:
        try:
            with open(rp, encoding="utf-8") as rf:
                outcome = (json.load(rf).get("oracle_result") or {}).get("outcome_score")
        except Exception:
            outcome = None
    rs = receipts(hid, task)
    return {
        "tag": tag, "arm": arm, "replica": k, "task": task, "hid": hid, "rc": proc.returncode,
        "usd": solve_usd(hid, task), "real_usd": solve_real_usd(tag, hid, task), "outcome": outcome, "seconds": secs, "receiptless": not rs,
        "endings": [r.get("ending") for r in rs],
        "stderr_tail": (proc.stderr or "")[-300:] if proc.returncode != 0 else "",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True, choices=sorted(MODELS))
    ap.add_argument("--phase", required=True, choices=("pilot", "rest"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--model-cap", type=float, default=2.50, help="PREREGISTRATION section 6: projection cap")
    args = ap.parse_args()
    pin_legacy_defaults()
    HOMES.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    write_config(args.tag)

    full = list(product(ARMS, TASKS, REPLICAS))
    random.Random(SEED).shuffle(full)  # one shuffle of the FULL design; the pilot is its head
    queue = full[:PILOT_N] if args.phase == "pilot" else full

    if args.phase == "rest":
        # Section 6: project from the kept pilot; cut to whole tasks (TASKS order) if it would not fit.
        pil = [solve_real_usd(args.tag, hid_of(args.tag, a, k), t) for a, t, k in full[:PILOT_N]]
        pil = [x for x in pil if x is not None]
        if len(pil) < PILOT_N // 2:
            sys.exit("pilot has too few receipts; run --phase pilot first and inspect it")
        mean = statistics.mean(pil)
        proj = mean * len(full)
        keep = TASKS
        proj *= SAFETY
        if proj > args.model_cap:
            n_tasks = max(1, int(args.model_cap / (mean * SAFETY * 2 * len(REPLICAS))))
            keep = TASKS[:n_tasks]
            print(f"CUT: pilot mean {mean:.4f} x {len(full)} = {proj:.2f} > cap {args.model_cap}; "
                  f"keeping the first {n_tasks} tasks in TASKS order", flush=True)
        else:
            print(f"pilot mean {mean:.4f} x {len(full)} = {proj:.2f} <= cap {args.model_cap}: full design", flush=True)
        queue = [(a, t, k) for (a, t, k) in full if t in keep]

    pending = [(a, t, k) for (a, t, k) in queue if not result_path(hid_of(args.tag, a, k), t)]
    print(f"tag={args.tag} phase={args.phase} queue={len(queue)} pending={len(pending)} seed={SEED} "
          f"spent_so_far={total_spent():.3f}", flush=True)

    lock = threading.Lock()
    spent = total_spent()
    done = bad = 0
    halt = False
    it = iter(pending)
    fh = DRIVER_LOG.open("a", encoding="utf-8")

    def submit_next(ex: ThreadPoolExecutor, futs: set) -> None:
        nonlocal halt
        try:
            a, t, k = next(it)
        except StopIteration:
            return
        if halt or spent >= HARD_STOP_USD:
            halt = True
            return
        futs.add(ex.submit(run_one, hid_of(args.tag, a, k), t, args.tag, a, k))

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
                    spent = total_spent()
                    bad += int(r["rc"] != 0 or r["receiptless"])
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    print(f"[{done}/{len(pending)}] {r['task']:<30} {r['arm']:<6} r{r['replica']} rc={r['rc']} "
                          f"real=${r['real_usd']} out={r['outcome']} {r['seconds']}s | spent ${spent:.3f} bad={bad}", flush=True)
                    if r["rc"] != 0 and r["stderr_tail"]:
                        print(f"    stderr: {r['stderr_tail']}", flush=True)
                    if spent >= HARD_STOP_USD:
                        halt = True
                        print(f"HALT: spend ${spent:.2f} >= hard stop ${HARD_STOP_USD}", flush=True)
                    if done >= 20 and bad / done > 0.05:
                        halt = True
                        print(f"HALT: {bad}/{done} rc!=0-or-receiptless > 5%", flush=True)
                if not halt:
                    submit_next(ex, futs)
    fh.close()
    print(f"\nfinished: done={done} spent=US${total_spent():.3f} bad={bad} halted={halt}", flush=True)


if __name__ == "__main__":
    main()
