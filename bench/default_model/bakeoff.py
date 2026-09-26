"""The default-model bake-off on SWE-bench django. See PREREGISTRATION.md, committed before any paid call.

    python bakeoff.py slice <gold_report.json>        # freeze the slice from a gold validation
    python bakeoff.py probe                            # the network wall + one pinned call per arm
    python bakeoff.py pilot [--workers W]              # every arm on the first PILOT_N slice items
    python bakeoff.py size                             # the registered sizing rule, from the pilot
    python bakeoff.py main --n N --arms A,D,G,Q [--workers W]   # resumable
    python bakeoff.py predictions <pilot|main> <dir>   # harness input, one file per arm

Solves are subprocesses (`bakeoff_solve.py`) under a 1800 s wall clock, run from a flat pool of
(item, arm) tasks submitted item by item with the arm order rotated, so the arms of an item run in
the same minutes. Every solve appends one flushed JSON line; a relaunch skips every (item, arm)
already on file.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (REPO, REPO / "bench" / "prompt_overlays", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from bakeoff_arms import ARMS, ORDER, assert_frozen, cost  # noqa: E402

RESULTS = Path(os.environ.get("BAKEOFF_RESULTS") or (HERE / "results"))
POOL = REPO / "bench" / "prompt_overlays" / "results" / "pool_django.jsonl"
SLICE = RESULTS / "slice.jsonl"
PILOT = RESULTS / "pilot_solves.jsonl"
MAIN = RESULTS / "main_solves.jsonl"
#: Amendment 1: solves cut at a block's end, with the calls they had made. Never graded or paired;
#: their spend counts against the cap.
KILLED = RESULTS / "killed_solves.jsonl"
#: Amendment 3: the main run before the local-disk wall, kept as the record of the breach and of
#: what it cost; never graded into a comparison.
DISCARDED = (RESULTS / "discarded_main_before_amendment3.jsonl",
             RESULTS / "discarded_killed_before_amendment3.jsonl")
#: Amendment 5: rate-limited halts moved out of the primary file before their re-run; spend kept.
RATELIMITED = RESULTS / "ratelimited_solves.jsonl"
STRATA = ("<15 min fix", "15 min - 1 hour")

PILOT_N = 8
TIMEOUT_S = 1800
#: The cap is US$ 15.00 with the pilot and the probes. The driver stops admitting new items at this
#: spend (billed where OpenRouter reported it, computed otherwise, whichever is larger), which
#: leaves room for the solves in flight and for a gap between the published price and the bill.
STOP_AT_USD = 13.0
HEADROOM = 1.25
#: Amendment 4: no item is admitted while the host drive (C:, where WSL's disk lives) has less free.
DISK_FLOOR = int(1.5 * 1024**3)
_LOCK = threading.Lock()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every complete line. A last line still being written by the driver is skipped, never raised."""
    if not path.exists():
        return []
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    for i, line in enumerate(lines):
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i != len(lines) - 1:
                raise
    return rows


def rate_limited(row: dict[str, Any]) -> bool:
    """Amendment 5: a halt the provider caused by rate limiting (the gateway's CredentialRejectedError
    for RATE_LIMIT). It is the route's reliability, not the model's, and it is re-run after the pass."""
    return "rate-limited" in str(row.get("halted") or "")


def counts_as_halt(row: dict[str, Any]) -> bool:
    """What the per-arm halt rule counts (Amendment 5): timeouts and every other error, not rate limits."""
    return bool(row.get("halted")) and not rate_limited(row)


def solve_usd(row: dict[str, Any], *, calls_key: str = "calls") -> float:
    """What a solve cost: the bill where every call reported one, else the computed price, and the
    larger of the two when both exist (the budget guard is conservative on purpose)."""
    calls = row.get(calls_key) or []
    computed = cost(calls, ARMS[row["arm"]])
    billed = [c.get("billed") for c in calls]
    if calls and all(isinstance(b, (int, float)) for b in billed):
        return max(computed, float(sum(billed)))  # type: ignore[arg-type]
    return computed


def unique_rows(path: Path) -> list[dict[str, Any]]:
    """The rows of a spend file, each solve once. The four solves stopped by hand for the vhdx
    compaction were recorded by the stop and again by the next driver's harvest of their call logs;
    a solve is its first call's generation id."""
    seen: set[tuple[str, str, str]] = set()
    out = []
    for r in load_jsonl(path):
        key = (r["instance_id"], r["arm"], str((r.get("calls") or [{}])[0].get("gen_id")))
        if key in seen and key[2] != "None":
            continue
        seen.add(key)
        out.append(r)
    return out


def spent() -> float:
    total = 0.0
    for path in (PILOT, MAIN, KILLED, RATELIMITED, *DISCARDED):
        for r in unique_rows(path):
            total += solve_usd(r) + solve_usd(r, calls_key="retry_calls")
    for name in ("probe.json", "probe_sandbox.json", "probe_sandbox_a4.json"):
        probe = RESULTS / name
        if probe.exists():
            total += float(json.loads(probe.read_text(encoding="utf-8")).get("usd", 0.0))
    return total


# ---------------------------------------------------------------- slice
def build_slice(gold_report: Path) -> None:
    """The gold-resolved candidates of bench/prompt_overlays' pool, in its registered sha256 order."""
    raw = gold_report.read_bytes()
    report = json.loads(raw)
    resolved = set(report.get("resolved_ids", []))
    cand = [r for r in load_jsonl(POOL) if r["difficulty"] in STRATA]
    kept = [r for r in cand if r["instance_id"] in resolved]
    dropped = [r["instance_id"] for r in cand if r["instance_id"] not in resolved]
    RESULTS.mkdir(parents=True, exist_ok=True)
    with SLICE.open("w", encoding="utf-8", newline="\n") as fh:
        for r in kept:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    meta = {"gold_report": str(gold_report), "gold_report_sha256": hashlib.sha256(raw).hexdigest(),
            "candidates": len(cand), "gold_resolved": len(kept), "dropped": dropped}
    (RESULTS / "slice_meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(meta, indent=2))


# ---------------------------------------------------------------- solving
def _child_env(home: Path) -> dict[str, str]:
    """bench/prompt_overlays' solve environment (Amendment 2's network wall), unchanged."""
    import run as po_run  # bench/prompt_overlays/run.py

    return po_run._child_env(home)


def _work_dir() -> Path:
    work = Path(os.environ["H45_WORK"])
    if not work.is_absolute() or len(work.parts) < 4 or "dflt" not in work.name:
        raise SystemExit(f"refusing work dir {work!s}")
    return work


def _rm(path: Path, under: Path) -> None:
    s = str(path)
    if s.startswith(str(under) + "/") and len(s) > len(str(under)) + 1:
        subprocess.run(["rm", "-rf", s], check=False)


#: Amendment 1: the solves in flight, so a block's end can kill them and record what they spent.
_IN_FLIGHT: dict[int, tuple[Path, dict[str, Any], str, Path]] = {}
#: Amendment 1: set for the main run only (see PREREGISTRATION.md). Behind the wall LiteLLM's
#: remote cost-map fetch always fails and it keeps its bundled copy; this skips the three retries.
_SOLVE_ENV_EXTRA: dict[str, str] = {}
#: Amendment 3: the local-disk wall. When set, every solve runs inside `sandbox.sh`'s namespaces
#: and its workspace is prepared here, by the driver, before it starts.
_SANDBOX = {"on": False}
SANDBOX_SH = HERE / "sandbox.sh"


def sandboxed(cmd: list[str], ws: Path, scratch: Path) -> list[str]:
    """`cmd` wrapped in the local-disk wall (Amendment 3)."""
    home = os.environ["HOME"]
    return ["unshare", "--user", "--map-root-user", "--mount", "--pid", "--fork", "--mount-proc",
            "bash", str(SANDBOX_SH), str(os.getuid()), str(os.getgid()), home,
            str(ws), str(scratch), str(REPO), f"{home}/.local/share/uv/python", "--", *cmd]


def _on_block_end(signum: int, frame: Any) -> None:
    """SIGTERM from the block's `timeout`: kill every solve in flight (each is its own session, so
    it would otherwise outlive the driver and collide with the next block's copy of it), record the
    calls each had made, and exit. Nothing partial is kept as a solve; its spend is kept.

    No lock and no print here. Both deadlocked on 2026-09-26: the handler runs in the main thread,
    which may be interrupted while holding the stdout buffer's lock, and `_LOCK` can be held by a
    worker in a slow write to the Windows drive. The dict is copied with a retry instead, and the
    last line goes out through os.write. The runner also kills 90 s after the soft signal."""
    flying: list[tuple[int, tuple[Path, dict[str, Any], str, Path]]] = []
    for _ in range(5):
        try:
            flying = list(_IN_FLIGHT.items())
            break
        except RuntimeError:  # the dict changed size while being copied
            continue
    for pid, (scratch, inst, arm, _ws) in flying:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
        log = scratch / "calls.jsonl"
        calls = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()
                 if ln.strip()] if log.exists() else []
        with KILLED.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"instance_id": inst["instance_id"], "arm": arm,
                                 "killed": "block end", "calls": calls}) + "\n")
    os.write(2, f"BLOCK END: killed {len(flying)} solves in flight\n".encode())
    os._exit(143)


def run_solve(inst: dict[str, Any], arm: str) -> dict[str, Any]:
    work = _work_dir()
    scratch = Path(tempfile.mkdtemp(prefix=f"dflt-{arm}-", dir=str(work)))
    inst_path, out_path = scratch / "instance.json", scratch / "out.json"
    home = scratch / "home"
    home.mkdir()
    inst_path.write_text(json.dumps(inst), encoding="utf-8")
    ws = work / f"{arm}__{inst['instance_id']}"
    env = _child_env(home)
    env["BAKEOFF_CALLS_LOG"] = str(scratch / "calls.jsonl")
    env.update(_SOLVE_ENV_EXTRA)
    cmd = [sys.executable, str(HERE / "bakeoff_solve.py"), str(inst_path), arm, str(out_path)]
    started = time.monotonic()
    if _SANDBOX["on"]:
        import solve_one as po  # bench/prompt_overlays

        try:
            po.prepare_workspace(inst, arm)
        except Exception as exc:  # noqa: BLE001 — a harness failure is a halt (PROTOCOL §2)
            row = {"instance_id": inst["instance_id"], "arm": arm, "calls": [], "patch": "",
                   "halted": f"workspace: {type(exc).__name__}: {exc}"[:400],
                   "seconds": round(time.monotonic() - started, 1)}
            _rm(ws, work)
            _rm(scratch, work)
            return row
        env["BAKEOFF_WS_READY"] = "1"
        (scratch / "tmp").mkdir(exist_ok=True)
        cmd = sandboxed(cmd, ws, scratch)
    with _LOCK:
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                start_new_session=True)
        _IN_FLIGHT[proc.pid] = (scratch, inst, arm, ws)
    timed_out = False
    try:
        _, err = proc.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(proc.pid, signal.SIGKILL)
        _, err = proc.communicate()
    finally:
        with _LOCK:
            _IN_FLIGHT.pop(proc.pid, None)
    if out_path.exists() and not timed_out:
        row = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        row = {"instance_id": inst["instance_id"], "arm": arm, "calls": [],
               "halted": "timeout" if timed_out else f"no output (exit {proc.returncode}): {(err or b'')[-300:]!r}"}
        if ws.exists():
            diff = subprocess.run(["git", "-C", str(ws), "diff", inst["base_commit"]], capture_output=True,
                                  text=True, errors="replace", check=False)
            row["patch"] = diff.stdout if diff.returncode == 0 else ""
        row["seconds"] = round(time.monotonic() - started, 1)
    row["usd"] = round(solve_usd(row), 6)
    row["difficulty"] = inst.get("difficulty")
    _rm(ws, work)
    _rm(scratch, work)
    return row


def solve_with_retry(inst: dict[str, Any], arm: str) -> dict[str, Any]:
    """A halt that is not the wall clock (a provider or harness error) is re-run once, fresh."""
    row = run_solve(inst, arm)
    if row.get("halted") and row["halted"] != "timeout":
        first = row
        row = run_solve(inst, arm)
        row["retried_after"] = first["halted"]
        row["retry_calls"] = first.get("calls") or []
    return row


def rotated(index: int, arms: list[str]) -> list[str]:
    k = index % len(arms)
    return arms[k:] + arms[:k]


def ensure_reference() -> None:
    ref = Path(os.environ["H45_DJANGO_REF"])
    if not (ref / ".git").exists():
        raise SystemExit(f"no django reference at {ref}; the setup script clones it")


def run_items(items: list[tuple[int, dict[str, Any]]], arms: list[str], out: Path, workers: int,
              start_until: float = 0.0) -> None:
    """``start_until`` (Amendment 1): seconds into this block after which no solve STARTS; a solve
    not started runs in the next block. 0 means no cutoff (the pilot ran without one)."""
    from solve_one import build_template, template_path  # bench/prompt_overlays

    assert_frozen()
    ensure_reference()
    work = _work_dir()
    # No solve of this bench is in flight when a driver starts (the runner checks), so whatever a
    # killed block left is garbage, except what it spent. A block killed from outside (the WSL VM
    # crashed at 07:39 on 2026-09-26) never ran its SIGTERM handler, so its solves' call logs are
    # read here, before their scratch dirs go.
    harvested = 0
    for leftover in sorted(work.glob("dflt-*")):
        log, inst_file = leftover / "calls.jsonl", leftover / "instance.json"
        arm = leftover.name.split("-")[1] if leftover.name.count("-") >= 2 else ""
        if log.exists() and inst_file.exists() and arm in ARMS:
            calls = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
            iid = json.loads(inst_file.read_text(encoding="utf-8"))["instance_id"]
            with KILLED.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps({"instance_id": iid, "arm": arm, "killed": "found at driver start",
                                     "calls": calls}) + "\n")
            harvested += 1
        _rm(leftover, work)
    # Disk stays bounded (2026-09-26: the host disk filled): workspaces and templates a killed block
    # left behind go too. Each is a whole django checkout.
    for leftover in [*work.glob("*__django__django-*"), *(work / "templates").glob("*")]:
        _rm(leftover, work)
    if harvested:
        print(f"recorded the calls of {harvested} solves a killed block left behind", flush=True)
    signal.signal(signal.SIGTERM, _on_block_end)
    block_start = time.monotonic()
    done = {(r["instance_id"], r["arm"]) for r in load_jsonl(out)}
    state: dict[str, Any] = {"stopped": "", "usd": spent()}
    admitted: dict[str, bool] = {}
    item_lock: dict[str, threading.Lock] = defaultdict(threading.Lock)
    left: dict[str, int] = {}
    tally: dict[str, list[int]] = {a: [0, 0] for a in arms}
    halted_arms: set[str] = set()
    # The halt rule is over the arm's whole run, not one block: replay what is on file, in order.
    for r in load_jsonl(out):
        if r["arm"] in tally:
            t = tally[r["arm"]]
            t[0] += 1
            t[1] += int(counts_as_halt(r))
            if t[0] >= 20 and t[1] / t[0] > 0.10:
                halted_arms.add(r["arm"])
    if halted_arms:
        print(f"arms already stopped by the halt rule: {sorted(halted_arms)}", flush=True)
    print(f"spent so far US${state['usd']:.3f}; {len(done)} solves on file; workers {workers}", flush=True)

    tasks = []
    for idx, inst in items:
        todo = [a for a in rotated(idx, arms) if (inst["instance_id"], a) not in done]
        left[inst["instance_id"]] = len(todo)
        tasks.extend((idx, inst, a) for a in todo)

    def task(idx: int, inst: dict[str, Any], arm: str) -> str:
        iid = inst["instance_id"]
        try:
            # Checked first: after the cutoff an item must not be admitted or have its template built
            # (block 1 spent its last 25 minutes cloning templates for solves that never started).
            if start_until and time.monotonic() - block_start > start_until:
                return f"{arm}>"  # not started; the next block runs it
            with item_lock[iid]:
                if iid not in admitted:
                    # The budget is checked per ITEM, when its first arm starts, so an admitted item
                    # runs every arm and no pair is broken by the stop.
                    admitted[iid] = not state["stopped"] and state["usd"] < STOP_AT_USD
                    if not admitted[iid] and not state["stopped"]:
                        state["stopped"] = f"budget: computed spend reached US${STOP_AT_USD}"
                    # Disk guard (Amendment 4): the host drive filled once and WSL went down with it.
                    if admitted[iid] and Path("/mnt/c").is_dir() and shutil.disk_usage("/mnt/c").free < DISK_FLOOR:
                        admitted[iid] = False
                        state["stopped"] = "disk guard: C: has under 1.5 GB free"
                    if admitted[iid]:
                        try:
                            build_template(inst)
                        except Exception as exc:  # noqa: BLE001 — the solve retries it and records the halt
                            print(f"  template for {iid} failed here: {exc}", flush=True)
            if not admitted[iid]:
                return f"{arm}$"
            if arm in halted_arms:
                return f"{arm}-"
            if start_until and time.monotonic() - block_start > start_until:
                return f"{arm}>"  # the item was admitted before the cutoff; this arm waits for the next block
            row = solve_with_retry(inst, arm)
            row["item_index"] = idx
            with _LOCK:
                with out.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                    fh.flush()
                state["usd"] += row["usd"] + solve_usd(row, calls_key="retry_calls")
                t = tally[arm]
                t[0] += 1
                t[1] += int(counts_as_halt(row))
                if t[0] >= 20 and t[1] / t[0] > 0.10 and arm not in halted_arms:
                    halted_arms.add(arm)
                    print(f"  STOP RULE: arm {arm} halted on {t[1]}/{t[0]} solves; no more {arm} solves",
                          flush=True)
            flag = "H" if row.get("halted") else ("E" if not (row.get("patch") or "").strip() else "p")
            return f"{arm}{flag}{row.get('steps', '-')}"
        finally:
            with item_lock[iid]:
                left[iid] -= 1
                if left[iid] == 0:
                    tpl = template_path(inst)
                    if tpl.name == iid:
                        _rm(tpl, work)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(task, idx, inst, arm): (idx, inst, arm) for idx, inst, arm in tasks}
        pending, n = set(futures), 0
        while pending:
            # A bounded wait, not `as_completed`: the main thread must wake to run the SIGTERM
            # handler. Blocked in an untimed lock wait it ran only when the next solve finished,
            # which on 2026-09-26 left a stop request unanswered for minutes.
            finished, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            for fut in finished:
                n += 1
                idx, inst, arm = futures[fut]
                print(f"  [{n:>4}/{len(tasks)}] #{idx:<3} {inst['instance_id']:<24} {fut.result():<8} "
                      f"US${state['usd']:.3f}  {time.strftime('%H:%M:%S')}", flush=True)
    if state["stopped"]:
        print(f"STOPPED: {state['stopped']}", flush=True)
    if halted_arms:
        print(f"ARMS STOPPED BY THE HALT RULE: {sorted(halted_arms)}", flush=True)


# ---------------------------------------------------------------- probe
def probe(arms: list[str]) -> None:
    """PROTOCOL §1 and §4. From a solve's environment: four ways out must fail (bench/prompt_overlays'
    wall probe, same commands), and per arm one pinned call offering one tool must come back as a
    parsed tool call from the pinned provider."""
    work = _work_dir()
    scratch = Path(tempfile.mkdtemp(prefix="dflt-probe-", dir=str(work)))
    home = scratch / "home"
    home.mkdir()
    env = _child_env(home)
    tries = {
        "curl_github": "curl -sS -m 15 -o /dev/null -w '%{http_code}' https://raw.githubusercontent.com/django/django/main/README.rst",
        "pip_download": f"python3 -m pip download --no-deps -q -d {scratch} asgiref",
        "git_ls_remote": "timeout 20 git ls-remote https://github.com/django/django HEAD",
        "python_urllib": "python3 -c \"import urllib.request;print(urllib.request.urlopen('https://pypi.org/simple/django/',timeout=15).status)\"",
    }
    out: dict[str, Any] = {"wall": {}, "arms": {}}
    for name, cmd in tries.items():
        r = subprocess.run(["bash", "-c", cmd], env=env, capture_output=True, text=True, timeout=120, check=False)
        blocked = r.returncode != 0 or "200" not in (r.stdout or "")
        out["wall"][name] = {"blocked": blocked, "rc": r.returncode, "tail": ((r.stdout or "") + (r.stderr or ""))[-160:]}
        print(f"{name:<14} blocked={blocked} rc={r.returncode}", flush=True)
    usd = 0.0
    for arm in arms:
        code = (
            f"import sys, json; sys.path.insert(0, {str(HERE)!r}); import bakeoff_solve as s;"
            f"s._install_tap(); from bakeoff_arms import ARMS; a = ARMS[{arm!r}]; r = s.Recorder(a);"
            "tools = [{'type': 'function', 'function': {'name': 'read_file', 'description': 'Read a file.',"
            " 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}];"
            "res = r.complete([{'role': 'user', 'content': 'Use the tool to read setup.py.'}], model=a.model,"
            " temperature=0.2, tools=tools);"
            "print('PROBE', json.dumps({'calls': [[t.name, t.arguments] for t in (res.tool_calls or [])],"
            " 'rec': r.calls[-1]}))"
        )
        res = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                             timeout=300, check=False)
        line = next((ln for ln in res.stdout.splitlines() if ln.startswith("PROBE ")), "")
        got = json.loads(line[6:]) if line else {}
        rec = got.get("rec") or {}
        ok = bool(got.get("calls")) and got["calls"][0][0] == "read_file" and "path" in got["calls"][0][1]
        served = rec.get("provider") or ""
        on_pin = served.lower() == ARMS[arm].provider.lower()
        call_usd = cost([rec], ARMS[arm]) if rec else 0.0
        if isinstance(rec.get("billed"), (int, float)):
            call_usd = max(call_usd, float(rec["billed"]))
        usd += call_usd
        out["arms"][arm] = {"tool_call_parsed": ok, "served": served, "on_pin": on_pin, "rec": rec,
                            "calls": got.get("calls"), "tail": (res.stdout + res.stderr)[-400:] if not line else ""}
        print(f"arm {arm}: tool_call_parsed={ok} served={served!r} on_pin={on_pin} billed={rec.get('billed')} "
              f"computed={cost([rec], ARMS[arm]) if rec else None}", flush=True)
    out["wall_holds"] = all(v["blocked"] for v in out["wall"].values())
    out["arms_ok"] = all(v["tool_call_parsed"] and v["on_pin"] for v in out["arms"].values())
    out["usd"] = usd
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "probe.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("WALL HOLDS" if out["wall_holds"] else "WALL DOES NOT HOLD")
    print("ARMS OK" if out["arms_ok"] else "AN ARM FAILED ITS PREFLIGHT")
    _rm(scratch, work)


def probe_sandbox(arms: list[str]) -> None:
    """Amendment 3's probe: from inside the local-disk wall, what a solve can and cannot reach.
    Every try runs through the same wrapper a solve runs through, on a real workspace."""
    import solve_one as po  # bench/prompt_overlays

    assert_frozen()
    work = _work_dir()
    inst = load_jsonl(SLICE)[0]
    ws = po.prepare_workspace(inst, "P")
    scratch = Path(tempfile.mkdtemp(prefix="dflt-P-", dir=str(work)))
    home = scratch / "home"
    home.mkdir()
    env = _child_env(home)
    real_home = os.environ["HOME"]
    own = {ws.name, scratch.name}
    must_fail = {
        "curl_github": "curl -sS -m 15 -o /dev/null -w '%{http_code}' https://raw.githubusercontent.com/django/django/main/README.rst",
        "git_ls_remote": "timeout 20 git ls-remote https://github.com/django/django HEAD",
        "python_urllib": "python3 -c \"import urllib.request;print(urllib.request.urlopen('https://pypi.org/simple/django/',timeout=15).status)\"",
        "django_ref": f"ls {real_home}/dflt-cache/django-ref/django {real_home}/h45-cache/django-ref/django",
        "gold_logs": f"ls {real_home}/h45-grade/gold/logs {real_home}/dflt-grade",
        "hf_cache": f"ls {real_home}/.cache/huggingface",
        "other_template": f"ls {work}/templates",
        "windows_drive": "ls /mnt/c/Users",
        "docker": "curl -sS -m 10 --unix-socket /var/run/docker.sock http://localhost/version",
        # Amendment 4: the gaps the H4/H5 bench found in its own first sandbox (its Amendment 7).
        "wsl_bind_mounts": "ls /mnt/wsl/docker-desktop-bind-mounts",
        "umount_home": f"umount {real_home}",
        "umount_mnt": "umount /mnt",
    }
    must_hold = {
        # Amendment 4. /mnt holds nothing but the path down to the resolver file.
        "mnt_listing": "ls -A /mnt; ls -A /mnt/wsl",
        # Amendment 4. The solve is not root and holds no capability in any set.
        "no_capabilities": "id -u; grep -E '^Cap(Inh|Prm|Eff|Amb)' /proc/self/status",
        # Amendment 4. Even a fresh user+mount namespace of its own cannot lift a cover: the mounts
        # it inherits are locked. Both umounts must fail, and nothing may appear under them.
        "nested_uncover": (f"unshare -rm sh -c 'umount -l /mnt; a=$?; umount -l {real_home}; b=$?; "
                           f"echo \"$a $b\"; ls -A /mnt/wsl {real_home}' 2>/dev/null || echo 'unshare refused'"),
        # Amendment 4. Outputs are not recorded, so the probe looks the way a solve would: every git
        # repo, venv and answer-shaped file findable from /, outside the workspace.
        "answer_shaped_files": (f"find / \\( -path /proc -o -path /sys -o -path {ws} \\) -prune -o \\( "
                                "-name .git -o -name pyvenv.cfg -o -name patch.diff -o -name eval.sh "
                                "-o -name '*django__django*' -o -iname '*swe-bench*' -o -iname '*swe_bench*' "
                                "-o -iname '*sweb.eval*' \\) -print 2>/dev/null"),
        "home_listing": f"ls -A {real_home}; ls -A {work}",
        "django_copies": ("find / \\( -path /proc -o -path /sys \\) -prune -o -type f "
                          "-path '*/django/db/models/base.py' -print 2>/dev/null"),
        "processes": "ps -e --no-headers | wc -l",
        "identity": f"id -un; git -C {ws} status --porcelain | wc -l; git -C {ws} rev-list --all --count ^{inst['base_commit']}",
        "venv": f"{sys.executable} -c 'import chimera, litellm; print(\"imports ok\")'",
    }
    out: dict[str, Any] = {"must_fail": {}, "must_hold": {}, "arms": {}}
    for name, cmd in must_fail.items():
        r = subprocess.run(sandboxed(["bash", "-c", cmd], ws, scratch), env=env, capture_output=True,
                           text=True, timeout=300, check=False)
        blocked = r.returncode != 0 or (name == "curl_github" and "200" not in r.stdout)
        out["must_fail"][name] = {"blocked": blocked, "rc": r.returncode, "tail": (r.stdout + r.stderr)[-200:]}
        print(f"{name:<15} blocked={blocked} rc={r.returncode} {(r.stdout + r.stderr).strip()[-90:]!r}", flush=True)
    for name, cmd in must_hold.items():
        r = subprocess.run(sandboxed(["bash", "-c", cmd], ws, scratch), env=env, capture_output=True,
                           text=True, timeout=900, check=False)
        text = r.stdout.strip()
        resolv_tail = Path(os.path.realpath("/etc/resolv.conf")).name
        if name == "mnt_listing":
            ok = set(text.split()) <= {"wsl", resolv_tail}
        elif name == "no_capabilities":
            lines = text.splitlines()
            ok = (len(lines) == 5 and lines[0].isdigit() and lines[0] != "0"
                  and all(ln.split()[-1].strip("0") == "" for ln in lines[1:]))
        elif name == "nested_uncover":
            lines = text.splitlines()
            refused = lines[:1] == ["unshare refused"]
            codes = lines[0].split() if lines and not refused else []
            listed = set(" ".join(lines[1:]).replace(":", " ").split()) - {"/mnt/wsl", real_home}
            ok = refused or (len(codes) == 2 and "0" not in codes
                             and listed <= {resolv_tail, Path(work).name, ".local"})
        elif name == "answer_shaped_files":
            # Only the read-only code copy (an empty `git init` and the harness venv) may match.
            ok = all(line.startswith(str(REPO) + "/") for line in text.splitlines())
        elif name == "home_listing":
            # The work dir, the path down to the interpreter, and this solve's two dirs: nothing else.
            ok = set(text.split()) <= {Path(work).name, ".local", *own}
        elif name == "django_copies":
            ok = bool(text) and all(line.startswith(str(ws) + "/") for line in text.splitlines())
        elif name == "processes":
            ok = text.isdigit() and int(text) < 10
        elif name == "identity":
            lines = text.splitlines()
            ok = len(lines) == 3 and lines[0] == os.environ.get("USER", lines[0]) and lines[1:] == ["0", "0"]
        else:
            ok = "imports ok" in text
        out["must_hold"][name] = {"ok": ok, "stdout": text[-400:], "stderr": r.stderr[-200:]}
        print(f"{name:<15} ok={ok} {text[-160:]!r}", flush=True)
    usd = 0.0
    for arm in arms:
        code = (
            f"import sys, json; sys.path.insert(0, {str(HERE)!r}); import bakeoff_solve as s;"
            f"s._install_tap(); from bakeoff_arms import ARMS; a = ARMS[{arm!r}]; r = s.Recorder(a);"
            "tools = [{'type': 'function', 'function': {'name': 'read_file', 'description': 'Read a file.',"
            " 'parameters': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}}}];"
            "res = r.complete([{'role': 'user', 'content': 'Use the tool to read setup.py.'}], model=a.model,"
            " temperature=0.2, tools=tools);"
            "print('PROBE', json.dumps({'calls': [[t.name, t.arguments] for t in (res.tool_calls or [])],"
            " 'rec': r.calls[-1]}))"
        )
        res = subprocess.run(sandboxed([sys.executable, "-c", code], ws, scratch), env=env,
                             capture_output=True, text=True, timeout=300, check=False)
        line = next((ln for ln in res.stdout.splitlines() if ln.startswith("PROBE ")), "")
        got = json.loads(line[6:]) if line else {}
        rec = got.get("rec") or {}
        ok = bool(got.get("calls")) and got["calls"][0][0] == "read_file"
        on_pin = (rec.get("provider") or "").lower() == ARMS[arm].provider.lower()
        usd += max(cost([rec], ARMS[arm]), float(rec.get("billed") or 0.0)) if rec else 0.0
        out["arms"][arm] = {"tool_call_parsed": ok, "on_pin": on_pin, "rec": rec,
                            "tail": "" if line else (res.stdout + res.stderr)[-400:]}
        print(f"arm {arm}: tool_call_parsed={ok} on_pin={on_pin} served={rec.get('provider')!r}", flush=True)
    out["wall_holds"] = (all(v["blocked"] for v in out["must_fail"].values())
                         and all(v["ok"] for v in out["must_hold"].values()))
    out["arms_ok"] = all(v["tool_call_parsed"] and v["on_pin"] for v in out["arms"].values())
    out["usd"] = usd
    # Amendment 4 writes its own file; the Amendment 3 probe stays as it was read.
    (RESULTS / "probe_sandbox_a4.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print("WALL HOLDS" if out["wall_holds"] else "WALL DOES NOT HOLD")
    print("ARMS OK" if out["arms_ok"] else "AN ARM FAILED ITS PREFLIGHT")
    _rm(ws, work)
    _rm(scratch, work)


def requeue_ratelimited() -> None:
    """Amendment 5: after the main pass, move every rate-limited halt out of the primary file (into
    RATELIMITED, spend kept), so the next `main` run solves that (item, arm) again, fresh. Refuses to
    run while a driver is alive: the file is rewritten, and a driver appends to it."""
    alive = subprocess.run(["pgrep", "-f", "default_model/bakeoff.py main"], capture_output=True, text=True)
    if alive.stdout.strip():
        raise SystemExit("a main driver is alive; refusing to rewrite the solves file")
    rows = load_jsonl(MAIN)
    moved = [r for r in rows if rate_limited(r)]
    kept = [r for r in rows if not rate_limited(r)]
    nl = "\n"
    with RATELIMITED.open("a", encoding="utf-8", newline=nl) as fh:
        for r in moved:
            fh.write(json.dumps(r, ensure_ascii=False) + nl)
    tmp = MAIN.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + nl for r in kept), encoding="utf-8", newline=nl)
    tmp.replace(MAIN)
    print(f"moved {len(moved)} rate-limited halts to {RATELIMITED.name}; {len(kept)} rows stay", flush=True)


# ---------------------------------------------------------------- sizing
def size() -> None:
    """The registered sizing rule (PREREGISTRATION.md, "Sizing"), read off the pilot's solves."""
    rows = load_jsonl(PILOT)
    slice_n = len(load_jsonl(SLICE))
    per_arm: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        per_arm[r["arm"]].append(solve_usd(r))
    c = {a: sum(v) / len(v) for a, v in per_arm.items() if v}
    s = spent()
    per_item = sum(c.values())
    n = min(slice_n, int((STOP_AT_USD - s) / (HEADROOM * per_item))) if per_item else 0
    print(json.dumps({"mean_usd_per_solve": {k: round(v, 5) for k, v in sorted(c.items())},
                      "usd_per_item_all_arms": round(per_item, 5), "spent": round(s, 4),
                      "slice": slice_n, "n": n}, indent=2))


def dry() -> None:
    """US$ 0: build the first slice item's workspace per arm and compose each arm's system message
    without calling a model. Shows the wall's git half, the tool list, and that the arms send the
    same system message."""
    import hashlib as _h

    import solve_one as po  # bench/prompt_overlays

    from chimera.core.agent import Agent, AgentConfig
    from chimera.governance.allowlist import restrict_registry
    from chimera.tools.builtin import default_registry

    assert_frozen()
    ensure_reference()
    inst = load_jsonl(SLICE)[0]
    for name in ORDER:
        ws = po.prepare_workspace(inst, name)
        tools = restrict_registry(default_registry(ws, host_exec_confirm=None), allow=po.CODING_TOOLS)
        agent = Agent(object(), tools, AgentConfig(model=ARMS[name].model, max_steps=30,  # type: ignore[arg-type]
                                                   insist_on_action=True, project_root=ws,
                                                   turn_context=True, prefix_nonce=""))
        system = agent.compose_system_prompt(po.INSTRUCTION.format(problem=inst["problem_statement"]))
        print(f"arm {name}: sha {_h.sha256(system.encode()).hexdigest()[:12]} temperature "
              f"{agent.config.temperature} tools {sorted(t.name for t in tools.tools())}")
        _rm(ws, _work_dir())
    _rm(po.template_path(inst), _work_dir())


# ---------------------------------------------------------------- grading input
def predictions(phase: str, outdir: Path) -> None:
    rows = load_jsonl(PILOT if phase == "pilot" else MAIN)
    outdir.mkdir(parents=True, exist_ok=True)
    by_arm: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r.get("halted") and r["halted"] != "timeout":
            continue  # a halt is not graded (PROTOCOL §2); a timeout is graded for the sensitivity reading
        by_arm.setdefault(r["arm"], []).append(
            {"instance_id": r["instance_id"], "model_name_or_path": f"dflt-{phase}-{r['arm']}",
             "model_patch": r.get("patch") or ""})
    for arm, preds in by_arm.items():
        path = outdir / f"predictions_{phase}_{arm}.jsonl"
        path.write_text("".join(json.dumps(p) + "\n" for p in preds), encoding="utf-8", newline="\n")
        print(f"{path}: {len(preds)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("slice")
    s.add_argument("gold", type=Path)
    pb = sub.add_parser("probe")
    pb.add_argument("--arms", default=",".join(ORDER))
    pb.add_argument("--sandbox", action="store_true")
    p = sub.add_parser("pilot")
    p.add_argument("--workers", type=int, default=16)
    sub.add_parser("size")
    sub.add_parser("dry")
    sub.add_parser("requeue-ratelimited")
    m = sub.add_parser("main")
    m.add_argument("--n", type=int, required=True)
    m.add_argument("--arms", default=",".join(ORDER))
    m.add_argument("--workers", type=int, default=16)
    m.add_argument("--start-until", type=float, default=1500.0)
    pr = sub.add_parser("predictions")
    pr.add_argument("phase", choices=("pilot", "main"))
    pr.add_argument("outdir", type=Path)
    args = ap.parse_args()
    if args.cmd == "slice":
        build_slice(args.gold)
    elif args.cmd == "probe" and args.sandbox:
        probe_sandbox(args.arms.split(","))
    elif args.cmd == "probe":
        probe(args.arms.split(","))
    elif args.cmd == "pilot":
        run_items(list(enumerate(load_jsonl(SLICE)[:PILOT_N])), list(ORDER), PILOT, args.workers)
    elif args.cmd == "size":
        size()
    elif args.cmd == "dry":
        dry()
    elif args.cmd == "requeue-ratelimited":
        requeue_ratelimited()
    elif args.cmd == "main":
        arms = [a for a in args.arms.split(",") if a]
        if "A" not in arms or any(a not in ARMS for a in arms):
            raise SystemExit(f"bad arms {arms}")
        _SOLVE_ENV_EXTRA["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # Amendment 1
        _SANDBOX["on"] = True  # Amendment 3
        run_items(list(enumerate(load_jsonl(SLICE)[: args.n])), arms, MAIN, args.workers,
                  start_until=args.start_until)
    elif args.cmd == "predictions":
        predictions(args.phase, args.outdir)
