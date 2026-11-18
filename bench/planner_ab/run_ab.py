"""Drive the planner A/B (bench/planner_ab/PREREGISTRATION.md) for one model, in WSL.

  ~/hb-venv/bin/python run_ab.py --tag glm --phase pilot            # first 12 triples, kept in the data
  ~/hb-venv/bin/python run_ab.py --tag glm --phase rest --model-cap 2.50

Arms: `plan` = chimera solve as is, `noplan` = `--no-plan`. Harness id `p101-<tag>-<arm>-r<k>`.
Each solve is one `harnessbench run-task` (a multi-round task runs its rounds inside it). Resumable:
a solve whose result json exists is skipped. Spend is read from the receipts under ~/p101-homes, never
from the provider's key counter (shared with other items). Finish E2 before deciding whether
the E3 pilot leaves room for its full design. Stop rules are in PREREGISTRATION.md section 6.
Retain the design decisions and interrupted attempts under ~/p101-homes when resuming.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import math
import os
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from itertools import chain, product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _legacy_defaults import LEGACY_VALUES, pin_legacy_defaults

HB = Path(os.path.expanduser("~/harness-bench"))
HOMES = Path(os.path.expanduser("~/p101-homes"))
LOGS = Path(os.path.expanduser("~/p101-logs"))
PY = os.path.expanduser("~/hb-venv/bin/python")
WRAPPER = str(Path.home() / "p101-solve.sh")
DRIVER_LOG = Path(os.path.expanduser("~/p101-driver.jsonl"))
CONFIG = HB / "config" / "harness.yaml"
DISCARDED_PILOT = HOMES.parent / "p101-discarded-pilot"
HARNESS_ENV = ("HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "OPENROUTER_API_KEY", *LEGACY_VALUES)

MODELS = {
    "glm": "openrouter/z-ai/glm-5.3-flash",
    "qwen": "openrouter/qwen/qwen3-coder-next",
}
ARMS = {"plan": [], "noplan": ["--no-plan"]}
REPLICAS = (0, 1, 2)
SEED = 20261116
TOTAL_CAP_USD = 5.00
E3_RESERVE_USD = 0.50
HARD_STOP_USD = 4.00
SAFETY = 1.25  # Amendment 1: spend = token-priced cost x 1.25, no cache discount taken
#: USD per million tokens (in, out), OpenRouter list price on the day of registration (2026-11-16).
PRICE = {"glm": (0.0399, 0.0784), "qwen": (0.12, 0.80)}
PILOT_N = 12
ATTEMPT_ID = ".p101-attempt"
Triple = tuple[str, str, int]
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


def results_root() -> Path:
    return HB / "data_try6" / "results"


def result_path(hid: str, task: str) -> str | None:
    hits = glob.glob(str(results_root() / hid / "*" / f"{task}.json"))
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


def _read_jsonl(log: Path) -> list[dict]:
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _driver_rows(log: Path) -> list[dict]:
    rows = []
    seen: dict[str, dict] = {}
    for row in _read_jsonl(log):
        if identity := row.get("attempt_id"):
            if identity in seen:
                if row != seen[identity]:
                    sys.exit(f"conflicting driver records for attempt {identity}")
                continue
            seen[identity] = row
        rows.append(row)
    # Rows written by the old driver have no identity. A second row for the same task
    # and harness may be a real retry, so keep those events in their original order.
    return rows


def _attempt_id(home: Path) -> str:
    marker = home / ATTEMPT_ID
    return marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""


def _attempt_homes(homes: Path) -> Iterator[tuple[Path, bool]]:
    active = ((home, False) for home in homes.glob("*-p101-*") if home.is_dir())
    archived = ((home, True) for home in (homes / ".interrupted").glob("*/*") if home.is_dir())
    yield from chain(active, archived)


def _phase_spend(homes: Path, log: Path, in_flight: set[str], results: Path | None = None) -> float:
    """Price every attempt once, estimating missing receipts at their model/arm's running mean."""
    observed: dict[tuple[str, str], list[float]] = defaultdict(list)
    missing: Counter[tuple[str, str]] = Counter()
    logged_names = set()
    legacy_names = set()
    logged_attempts = set()
    for row in _driver_rows(log):
        name = f"{row['task']}-{row['hid']}"
        logged_names.add(name)
        if attempt := row.get("attempt_id"):
            logged_attempts.add(attempt)
        else:
            legacy_names.add(name)
        arm = (row["tag"], row["arm"])
        if row["receiptless"]:
            missing[arm] += 1
        else:
            # The driver already priced this solve from its receipts; retain it across retries.
            observed[arm].append(row["real_usd"])

    actual = set()
    if results is not None:
        actual = {f"{p.stem}-{p.parent.parent.name}" for p in results.glob("p101-*/*/*.json")}
    accounted = set()
    pending_usd = 0.0
    for home, retired in _attempt_homes(homes):
        name = home.name
        identity = _attempt_id(home)
        live = not retired and name in in_flight
        if not retired:
            accounted.add(name)
        if identity in logged_attempts:
            continue
        if not identity and name in legacy_names and not retired:
            if results is not None and name not in actual:
                sys.exit(f"cannot reconcile legacy spend for {name}: home has no attempt identity or harness result")
            continue
        tag, arm, _ = name.rsplit("-p101-", 1)[1].split("-")
        receipt = home / "runs.jsonl"
        rows = _read_jsonl(receipt)
        if live:
            pending_usd += tokens_usd(tag, rows)
        elif rows:
            observed[tag, arm].append(tokens_usd(tag, rows))
        else:
            # The previous process may have been interrupted after a provider call.
            missing[tag, arm] += 1

    # A harness result can survive an interruption that prevented a driver row from being written.
    for name in actual - logged_names - accounted - in_flight:
        tag, arm, _ = name.rsplit("-p101-", 1)[1].split("-")
        missing[tag, arm] += 1

    # With no observations for an arm that already ran, admission must wait for a price.
    if any(not observed[arm] for arm in missing):
        return float("inf")
    return (pending_usd + sum(map(sum, observed.values()))
            + sum(count * statistics.mean(observed[arm]) for arm, count in missing.items()))


def total_spent(in_flight: set[str]) -> float:
    """The item's ceiling includes both models and the discarded pilot (Amendment 1)."""
    return SAFETY * (
        _phase_spend(HOMES, DRIVER_LOG, in_flight, results_root())
        + _phase_spend(DISCARDED_PILOT / HOMES.name, DISCARDED_PILOT / DRIVER_LOG.name, set())
    )


def pilot_mean(tag: str, pilot: list[tuple[str, str, int]]) -> float:
    observed: dict[str, list[float]] = defaultdict(list)
    missing: Counter[str] = Counter()
    settled = {(row["hid"], row["task"]) for row in _driver_rows(DRIVER_LOG)}
    for arm, task, replica in pilot:
        hid = hid_of(tag, arm, replica)
        if (hid, task) not in settled and result_path(hid, task) is None:
            sys.exit("pilot is incomplete; run --phase pilot first and inspect it")
        usd = solve_real_usd(tag, hid, task)
        if usd is None:
            missing[arm] += 1
        else:
            observed[arm].append(usd)
    if sum(map(len, observed.values())) < PILOT_N // 2 or any(not observed[arm] for arm in missing):
        sys.exit("pilot has too few receipts; run --phase pilot first and inspect it")
    return (sum(map(sum, observed.values()))
            + sum(count * statistics.mean(observed[arm]) for arm, count in missing.items())) / len(pilot)


def _sync_dir(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _save_state(path: Path, payload: object) -> None:
    # Publish a complete decision before any submission, even across a process interruption.
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}-", delete=False,
    ) as temp:
        scratch = Path(temp.name)
        try:
            json.dump(payload, temp, allow_nan=False, sort_keys=True)
            temp.flush()
            os.fsync(temp.fileno())
        except BaseException:
            temp.close()
            scratch.unlink(missing_ok=True)
            raise
    try:
        os.replace(scratch, path)
        _sync_dir(path.parent)
    finally:
        scratch.unlink(missing_ok=True)


def _load_state(path: Path) -> dict | None:
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        sys.exit(f"invalid driver checkpoint: {path.name}")
    return state


def _manifest(tag: str, full: list[Triple]) -> str:
    settings = {
        "version": 1,
        "tag": tag,
        "model": MODELS[tag],
        "design": full,
        "seed": SEED,
        "pilot_n": PILOT_N,
        "flags": ARMS,
        "controls": {name: os.environ.get(name) for name in LEGACY_VALUES},
        "prices": PRICE,
        "safety": SAFETY,
        "limits": [TOTAL_CAP_USD, E3_RESERVE_USD, HARD_STOP_USD],
        "harness": str(HB.resolve()),
        "homes": str(HOMES.resolve()),
        "log": str(DRIVER_LOG.resolve()),
        "runner": PY,
        "wrapper": hashlib.sha256(Path(WRAPPER).read_bytes()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()


def _e2_queue(full: list[Triple], task_count: int) -> list[Triple]:
    return [(arm, task, replica) for arm, task, replica in full if task in TASKS[:task_count]]


def _e2_finished(full: list[Triple]) -> int:
    decision = _load_state(HOMES / ".e2-design.json")
    task_count = len(TASKS)
    if decision is not None:
        task_count = decision["task_count"]
        if (decision["manifest"] != _manifest("glm", full)
                or type(task_count) is not int or not (1 <= task_count <= len(TASKS))):
            sys.exit("E2 design checkpoint does not match the current design")
    # A legacy experiment without a checkpoint can prove completion only for the full design.
    required = set(full[:PILOT_N]) | set(_e2_queue(full, task_count))
    if any(result_path(hid_of("glm", arm, replica), task) is None for arm, task, replica in required):
        sys.exit("finish E2's admitted design, including its pilot, before admitting E3")
    return task_count


def _e3_admitted(full: list[Triple], spent: float) -> bool:
    e2_tasks = _e2_finished(full)
    path = HOMES / ".e3-admission.json"
    manifest = _manifest("qwen", full)
    decision = _load_state(path)
    if decision is not None:
        if decision["manifest"] != manifest or decision["e2_tasks"] != e2_tasks:
            sys.exit("E3 admission checkpoint does not match the current design")
        admitted = decision["admitted"]
        original_spend = decision["spent"]
        projected = decision["projected"]
        remaining = TOTAL_CAP_USD - original_spend - E3_RESERVE_USD
        if (type(admitted) is not bool or not math.isfinite(original_spend) or original_spend < 0
                or not math.isfinite(projected) or projected < 0 or decision["remaining"] != remaining
                or admitted != (projected <= remaining)):
            sys.exit("E3 admission checkpoint contains inconsistent budget figures")
        print(f"E3: resuming recorded decision (admitted={admitted})", flush=True)
        return admitted

    after_pilot = {(hid_of("qwen", arm, replica), task) for arm, task, replica in full[PILOT_N:]}
    logged = {(row["hid"], row["task"]) for row in _driver_rows(DRIVER_LOG)}
    if (after_pilot & logged or any(
        result_path(hid, task) is not None or (HOMES / f"{task}-{hid}").exists()
        for hid, task in after_pilot
    )):
        sys.exit("E3 already has work beyond the pilot without a saved admission decision")
    if not math.isfinite(spent):
        sys.exit("E3 cannot be admitted until the preceding spend is reconciled")

    mean = pilot_mean("qwen", full[:PILOT_N]) * SAFETY
    projected = mean * len(full)
    remaining = TOTAL_CAP_USD - spent - E3_RESERVE_USD
    admitted = projected <= remaining
    _save_state(path, {
        "manifest": manifest, "e2_tasks": e2_tasks, "spent": spent,
        "projected": projected, "remaining": remaining, "admitted": admitted,
    })
    verdict = "full design" if admitted else "drop E3"
    print(f"E3: projection USD {projected:.2f}, available USD {remaining:.2f}: {verdict}", flush=True)
    return admitted


def _prepare_attempt(name: str) -> None:
    home = HOMES / name
    if home.exists():
        # A worker may have paid and left receipts before producing a result. Moving the home
        # within the same filesystem makes the old evidence survive even if this retry is stopped.
        archive = HOMES / ".interrupted" / uuid.uuid4().hex
        archive.mkdir(parents=True)
        _sync_dir(archive.parent)
        home.replace(archive / name)
        _sync_dir(archive)
        _sync_dir(HOMES)
    home.mkdir()
    _assign_attempt_id(home)
    _sync_dir(HOMES)
    (LOGS / f"{name}.log").unlink(missing_ok=True)


def _assign_attempt_id(home: Path) -> str:
    identity = uuid.uuid4().hex
    with (home / ATTEMPT_ID).open("w", encoding="utf-8") as marker:
        marker.write(identity)
        marker.flush()
        os.fsync(marker.fileno())
    _sync_dir(home)
    return identity


def _recover_apparatus(tag: str) -> list[dict]:
    rows = _driver_rows(DRIVER_LOG)
    logged_ids = {row["attempt_id"] for row in rows if row.get("attempt_id")}
    logged_names = {f"{row['task']}-{row['hid']}" for row in rows}
    legacy_names = {f"{row['task']}-{row['hid']}" for row in rows if not row.get("attempt_id")}
    interrupted = []

    def recover(task: str, hid: str, identity: str, receipts: list[dict]) -> None:
        if identity in logged_ids:
            return
        _, model_tag, arm, replica = hid.split("-")
        interrupted.append({
            "tag": model_tag, "arm": arm, "replica": int(replica[1:]), "task": task, "hid": hid,
            "attempt_id": identity, "rc": None, "receiptless": not receipts,
            "outcome": None, "real_usd": tokens_usd(model_tag, receipts) if receipts else None,
            "recovered": True,
        })
        logged_names.add(f"{task}-{hid}")
        logged_ids.add(identity)

    for home, retired in sorted(_attempt_homes(HOMES)):
        task, suffix = home.name.rsplit("-p101-", 1)
        if not suffix.startswith(f"{tag}-"):
            continue
        identity = _attempt_id(home)
        if not identity and not retired and home.name in legacy_names:
            continue  # Spending reconciliation checks ambiguous unmarked legacy homes.
        if identity not in logged_ids:
            recover(task, f"p101-{suffix}", identity or _assign_attempt_id(home), _read_jsonl(home / "runs.jsonl"))
    for result in sorted(results_root().glob(f"p101-{tag}-*/*/*.json")):
        hid = result.parent.parent.name
        name = f"{result.stem}-{hid}"
        if name not in logged_names:
            recover(result.stem, hid, f"harness:{name}", [])

    # A stopped driver has no harness rc for these attempts. Count them as apparatus failures
    # while retaining receipt cost and leaving the oracle outcome unread.
    if interrupted:
        with DRIVER_LOG.open("a", encoding="utf-8") as log:
            for row in interrupted:
                log.write(json.dumps(row) + "\n")
            log.flush()
            os.fsync(log.fileno())
        _sync_dir(DRIVER_LOG.parent)
    return rows + interrupted


def _apparatus_bad(row: dict) -> bool:
    return row["rc"] != 0 or bool(row["receiptless"])


def _apparatus_crossed(done: int, bad: int) -> bool:
    return done >= 20 and bad * 20 > done


def _apparatus_progress(rows: list[dict], tag: str) -> tuple[int, int, tuple[int, int] | None]:
    done = bad = 0
    crossed = None
    for row in rows:
        if row["tag"] != tag:
            continue
        done += 1
        bad += int(_apparatus_bad(row))
        if crossed is None and _apparatus_crossed(done, bad):
            crossed = (done, bad)
    return done, bad, crossed



def run_one(hid: str, task: str, tag: str, arm: str, k: int, driver_fd: int | None = None) -> dict:
    env = {name: os.environ[name] for name in HARNESS_ENV if name in os.environ}
    env["PYTHONPATH"] = "src"
    t0 = time.monotonic()
    proc = subprocess.run([PY, "-m", "harnessbench.cli", "run-task", "--task", task, "--harness", hid],
                          cwd=str(HB), env=env, capture_output=True, text=True, timeout=3600,
                          pass_fds=() if driver_fd is None else (driver_fd,))
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
    ap.add_argument("--concurrency", type=int, choices=range(1, 5), default=4)
    ap.add_argument("--model-cap", type=float, default=2.50, help="PREREGISTRATION section 6: E2 projection cap")
    args = ap.parse_args()
    pin_legacy_defaults()
    HOMES.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    with (HOMES / ".driver.lock").open("a", encoding="utf-8") as lock:
        driver_fd = None
        if os.name == "posix":
            import fcntl

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                sys.exit("another planner A/B driver or harness invocation is still running")
            # The harness retains the lock when the driver exits unexpectedly. A resume
            # must not recycle a home while its previous attempt can still write there.
            driver_fd = lock.fileno()
        _run(args, driver_fd)


def _run(args: argparse.Namespace, driver_fd: int | None) -> None:
    rows = _recover_apparatus(args.tag)
    done, bad, crossed = _apparatus_progress(rows, args.tag)
    if crossed is not None:
        at_done, at_bad = crossed
        print(f"HALT: {args.tag} apparatus already crossed the stop at {at_bad}/{at_done} "
              f"(now {bad}/{done}); investigate before continuing", flush=True)
        return
    write_config(args.tag)

    full = list(product(ARMS, TASKS, REPLICAS))
    random.Random(SEED).shuffle(full)  # one shuffle of the FULL design; the pilot is its head
    queue = full[:PILOT_N] if args.phase == "pilot" else full

    in_flight: set[str] = set()
    spent = total_spent(in_flight)
    if args.phase == "rest":
        if args.tag == "qwen":
            if not _e3_admitted(full, spent):
                return
        else:
            path = HOMES / ".e2-design.json"
            manifest = _manifest("glm", full)
            decision = _load_state(path)
            if decision is None:
                mean = pilot_mean("glm", full[:PILOT_N]) * SAFETY
                projected = mean * len(full)
                if projected > args.model_cap:
                    # E2 alone has a pre-registered cut to whole tasks in TASKS order.
                    n_tasks = max(1, int(args.model_cap / (mean * 2 * len(REPLICAS))))
                else:
                    n_tasks = len(TASKS)
                decision = {"manifest": manifest, "model_cap": args.model_cap, "task_count": n_tasks}
                _save_state(path, decision)
                print(f"E2: projection USD {projected:.2f}, cap USD {args.model_cap}: "
                      f"{n_tasks}/{len(TASKS)} tasks", flush=True)
            elif (decision["manifest"] != manifest or decision["model_cap"] != args.model_cap
                  or type(decision["task_count"]) is not int or not (1 <= decision["task_count"] <= len(TASKS))):
                sys.exit("E2 design checkpoint does not match the current design or projection cap")
            queue = _e2_queue(full, decision["task_count"])

    pending = [(a, t, k) for (a, t, k) in queue if not result_path(hid_of(args.tag, a, k), t)]
    print(f"tag={args.tag} phase={args.phase} queue={len(queue)} pending={len(pending)} seed={SEED} "
          f"spent_so_far={spent:.3f}", flush=True)

    lock = threading.Lock()
    processed = 0
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
        hid = hid_of(args.tag, a, k)
        name = f"{t}-{hid}"
        _prepare_attempt(name)
        in_flight.add(name)
        futs.add(ex.submit(run_one, hid, t, args.tag, a, k, driver_fd))

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs: set = set()
        for _ in range(args.concurrency):
            submit_next(ex, futs)
        while futs:
            got, futs = wait(futs, return_when=FIRST_COMPLETED)
            for f in got:
                r = f.result()
                with lock:
                    processed += 1
                    done += 1
                    bad += int(_apparatus_bad(r))
                    name = f"{r['task']}-{r['hid']}"
                    r["attempt_id"] = _attempt_id(HOMES / name)
                    fh.write(json.dumps(r) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
                    in_flight.remove(name)
                    spent = total_spent(in_flight)
                    print(f"[{processed}/{len(pending)}] {r['task']:<30} {r['arm']:<6} r{r['replica']} rc={r['rc']} "
                          f"real=${r['real_usd']} out={r['outcome']} {r['seconds']}s | "
                          f"spent ${spent:.3f} apparatus bad={bad}/{done}", flush=True)
                    if r["rc"] != 0 and r["stderr_tail"]:
                        print(f"    stderr: {r['stderr_tail']}", flush=True)
                    if spent >= HARD_STOP_USD:
                        halt = True
                        print(f"HALT: spend ${spent:.2f} >= hard stop ${HARD_STOP_USD}", flush=True)
                    if _apparatus_crossed(done, bad):
                        halt = True
                        print(f"HALT: {bad}/{done} rc!=0-or-receiptless > 5%", flush=True)
                if not halt:
                    submit_next(ex, futs)
    fh.close()
    print(f"\nfinished: done={done} spent=US${total_spent(in_flight):.3f} bad={bad} halted={halt}", flush=True)


if __name__ == "__main__":
    pin_legacy_defaults()  # the five 2026-11-10 defaults, back to the value the factorial used
    main()
