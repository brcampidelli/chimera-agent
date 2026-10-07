"""Decision latency under a queue — the run registered in `PREREGISTRATION.md`.

    python bench/decision_queue/run.py --check                   # items, seeds, call count; no call
    python bench/decision_queue/run.py --smoke --out <p.jsonl>   # 16 calls: checks the runner, not read
    python bench/decision_queue/run.py --run --out <p.jsonl>     # 880 measured + 6 warm-up calls
    python bench/decision_queue/run.py --report <p.jsonl>        # the registered readings and decision

Every call is the product's own: `Decider(LocalLogprobBackend, shipped maps).decide("governance.danger",
state, DANGER)`, with no cache and no deadline, one backend (one HTTP client) per concurrent client.
The model is unloaded at the end only if it was not loaded when the run started.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import httpx  # noqa: E402

from bench.governance_judge.corpus import corpus as corpus_easy  # noqa: E402
from bench.governance_judge.corpus_ambiguous import corpus as corpus_ambiguous  # noqa: E402
from chimera.governance.governed_tool import render_action  # noqa: E402

OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen3:4b"
BUILD = "qwen3:4b@Q4_K_M"
TAU = 0.50
LEVELS = (1, 2, 4, 8)
SWEEPS = (("A", LEVELS), ("B", tuple(reversed(LEVELS))))
PASSES = 2
WARMUP = 3
DEADLINES = (0.5, 1.0)
#: The registered thresholds of the decision rule.
SUPPORTED_PHI = 0.95
DRIFT = 0.20
SERIAL_BAND = 0.25
BATCHED_AT = 1.5
GPU_BUSY_UTIL = 10.0
#: Amendment 2: with no OTHER process on the GPU, a busy reading is the previous sweep's own load
#: draining, so the guard waits for it — polling every 5 s, for at most 5 min — before refusing.
GPU_POLL_S = 5.0
GPU_WAIT_MAX_S = 300.0
#: How a backend timeout reads on the receipt. Such a call is a CENSORED latency (at least the
#: backend's 30 s), counted as a miss at every deadline — dropping it would report the latency of
#: the calls that were fast enough to answer (amendment in PREREGISTRATION.md).
TIMEOUT_HALTS = ("ReadTimeout", "WriteTimeout", "PoolTimeout", "ConnectTimeout", "TimeoutException")


def timed_out(row: dict[str, Any]) -> bool:
    return bool(row.get("halt")) and str(row["halt"]).split(":", 1)[0] in TIMEOUT_HALTS


def items() -> list[dict[str, Any]]:
    out = []
    for name, corpus in (("easy", corpus_easy), ("ambiguous", corpus_ambiguous)):
        for it in corpus():
            action, _doc = render_action("run_shell", {"command": it.command})
            out.append({"slice": name, "id": it.id, "label": it.label, "state": action})
    return out


def seed(sweep_index: int, c: int, pass_: int) -> int:
    """The registered shuffle seed of one pass of one cell."""
    return 1000 * sweep_index + 10 * c + pass_


def order(rows: list[dict[str, Any]], sweep_index: int, c: int) -> list[tuple[int, dict[str, Any]]]:
    out: list[tuple[int, dict[str, Any]]] = []
    for p in range(PASSES):
        shuffled = list(rows)
        random.Random(seed(sweep_index, c, p)).shuffle(shuffled)
        out.extend((p, r) for r in shuffled)
    return out


def check() -> list[dict[str, Any]]:
    rows = items()
    assert len(rows) == 55 and sum(r["label"] == "attack" for r in rows) == 24, "not the fitted corpus"
    assert len({r["id"] for r in rows}) == 55, "item ids repeat"
    for i, (_name, levels) in enumerate(SWEEPS):
        for c in levels:
            assert len(order(rows, i, c)) == PASSES * 55
    return rows


# --- the machine -------------------------------------------------------------------------------


def gpu_state() -> dict[str, Any]:
    """What `nvidia-smi` says: utilisation and any compute process that is not Ollama. ``known`` is
    False without the tool, and the run then refuses as it does on a busy GPU."""
    if shutil.which("nvidia-smi") is None:
        return {"known": False}
    try:
        util = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip().splitlines()[0]
        apps = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError, IndexError):
        return {"known": False}
    name, utilisation, temperature, memory = [x.strip() for x in util.split(",")]
    others = [a.strip() for a in apps if a.strip() and "ollama" not in a.lower() and "llama-server" not in a.lower()]
    return {"known": True, "gpu": name, "util": float(utilisation), "temp_c": float(temperature),
            "mem_mib": float(memory), "other_processes": others}


def gpu_idle(state: dict[str, Any]) -> bool:
    return bool(state.get("known")) and not state["other_processes"] and state["util"] < GPU_BUSY_UTIL


def wait_for_idle(
    read: Callable[[], dict[str, Any]] = lambda: gpu_state(),
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = GPU_POLL_S,
    max_wait_s: float = GPU_WAIT_MAX_S,
) -> dict[str, Any]:
    """The GPU state to start a sweep on, with ``idle``, ``waited_s``, ``polls`` and the temperature
    at the first and last reading.

    Another compute process, or no `nvidia-smi`, refuses at once: waiting cannot make someone else's
    job leave. Utilisation alone with nobody else on the GPU is our own previous sweep cooling off
    (the first full run stopped on 54% right after sweep A, with no other process), so it is polled
    until it falls under the threshold or the budget runs out. The wait is counted in readings, not
    wall time, so a test injects readings and a no-op sleep."""
    state = read()
    first_temp = state.get("temp_c")
    waited, polls = 0.0, 1
    while state.get("known") and not state["other_processes"] and not gpu_idle(state) and waited + poll_s <= max_wait_s:
        sleep(poll_s)
        waited += poll_s
        state = read()
        polls += 1
    return {**state, "idle": gpu_idle(state), "waited_s": waited, "polls": polls,
            "temp_c_first": first_temp, "temp_c_last": state.get("temp_c")}


def model_loaded(client: httpx.Client) -> bool:
    models = client.get(f"{OLLAMA}/api/ps", timeout=10).json().get("models") or []
    return any(m.get("name") == MODEL or m.get("model") == MODEL for m in models)


def unload(client: httpx.Client) -> None:
    client.post(f"{OLLAMA}/api/generate", json={"model": MODEL, "keep_alive": 0}, timeout=30)


def meta(client: httpx.Client) -> dict[str, Any]:
    try:
        version = client.get(f"{OLLAMA}/api/version", timeout=10).json().get("version")
    except (httpx.HTTPError, ValueError):
        version = None
    try:
        sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        sha = ""
    return {
        "kind": "meta", "at": time.time(), "ollama": version, "model": MODEL, "git": sha,
        # The runner's view; the server's own environment is not queryable (PREREGISTRATION.md).
        "OLLAMA_NUM_PARALLEL": os.environ.get("OLLAMA_NUM_PARALLEL") or "server default",
        "OLLAMA_MAX_QUEUE": os.environ.get("OLLAMA_MAX_QUEUE") or "server default",
        "levels": list(LEVELS), "passes": PASSES, "warmup": WARMUP,
    }


# --- the calls ---------------------------------------------------------------------------------


def _decider() -> Any:
    from chimera.decisions.calibration import CalibrationMaps
    from chimera.decisions.contract import Decider
    from chimera.decisions.local import LocalLogprobBackend

    # No cache: a cached reading costs nothing and would measure the cache. No deadline: a deadline
    # censors the tail this bench exists to see.
    return Decider(LocalLogprobBackend(OLLAMA, MODEL), CalibrationMaps.shipped())


def _row(answer: Any, item: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        **extra, "id": item["id"], "label": item["label"], "p": answer.p, "raw_p": answer.raw_p,
        "calibrated": answer.calibrated, "choice": answer.choice, "build": answer.resolved_model,
        "halt": answer.halt, "seconds": round(answer.seconds, 4),
    }


def cell(work: list[tuple[int, dict[str, Any]]], c: int, emit: Any, **tags: Any) -> None:
    """``c`` clients drain one shared queue; each has its own backend, as separate surfaces do."""
    from chimera.decisions.governance import DANGER, DECISION

    jobs: queue.Queue[tuple[int, int, dict[str, Any]]] = queue.Queue()
    for position, (pass_, item) in enumerate(work):
        jobs.put((position, pass_, item))
    t0 = time.perf_counter()

    def client(k: int) -> None:
        decider = _decider()
        while True:
            try:
                position, pass_, item = jobs.get_nowait()
            except queue.Empty:
                return
            started = time.perf_counter() - t0
            answer = decider.decide(DECISION, item["state"], DANGER)
            emit(_row(answer, item, **tags, c=c, client=k, pass_=pass_, position=position,
                      started=round(started, 4), ended=round(time.perf_counter() - t0, 4)))

    threads = [threading.Thread(target=client, args=(k,), name=f"client-{k}") for k in range(c)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    emit({**tags, "kind": "cell", "c": c, "calls": len(work), "wall": round(time.perf_counter() - t0, 4)})


def warmup(rows: list[dict[str, Any]], n: int, emit: Any, **tags: Any) -> None:
    from chimera.decisions.governance import DANGER, DECISION

    decider = _decider()
    for item in rows[:n]:
        emit(_row(decider.decide(DECISION, item["state"], DANGER), item, kind="warmup", **tags))


def run(out: Path, *, smoke: bool, allow_busy_gpu: bool) -> None:
    rows = check()
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    with httpx.Client() as http, out.open("w", encoding="utf-8", newline="\n") as fh:
        def emit(row: dict[str, Any]) -> None:
            with lock:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                if row.get("kind") == "cell":
                    print(f"  cell {row.get('sweep')} c={row['c']}: {row['calls']} calls in {row['wall']:.1f}s")

        was_loaded = model_loaded(http)
        emit({**meta(http), "smoke": smoke, "was_loaded": was_loaded})
        try:
            plan: list[tuple[str, int, list[tuple[int, dict[str, Any]]]]]
            if smoke:
                # 2 warm-up + 6 at c=1 + 8 at c=4 = 16 calls: does the runner work, nothing more.
                plan = [("S", 1, [(0, r) for r in rows[:6]]), ("S", 4, [(0, r) for r in rows[6:14]])]
                sweeps = [("S", plan)]
            else:
                sweeps = [(name, [(name, c, order(rows, i, c)) for c in levels]) for i, (name, levels) in enumerate(SWEEPS)]
            for name, cells in sweeps:
                gpu = wait_for_idle() if not allow_busy_gpu else {**gpu_state(), "waited_s": 0.0}
                busy = not gpu_idle(gpu)
                emit({"kind": "gpu", "sweep": name, **gpu, "idle": not busy})
                if busy and not allow_busy_gpu:
                    raise SystemExit(
                        f"GPU not idle before sweep {name} after {gpu.get('waited_s', 0):.0f}s: {gpu} "
                        "— the registered control refuses"
                    )
                warmup(rows, 2 if smoke else WARMUP, emit, sweep=name, busy_gpu=busy)
                for sweep_name, c, work in cells:
                    print(f"sweep {sweep_name} c={c}: {len(work)} calls")
                    cell(work, c, emit, kind="measured", sweep=sweep_name, busy_gpu=busy, smoke=smoke)
        finally:
            if not was_loaded:
                unload(http)
                emit({"kind": "unloaded", "at": time.time()})


# --- the report --------------------------------------------------------------------------------


def nearest_rank(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def _binom_cdf(k: int, n: int, q: float) -> float:
    return sum(math.comb(n, i) * q**i * (1 - q) ** (n - i) for i in range(0, k + 1))


def quantile_interval(xs: list[float], q: float, level: float = 0.95) -> tuple[float, float] | None:
    """Distribution-free order-statistic interval for the q-quantile (ranks from the binomial), or
    None when n is too small to reach ``level``."""
    s = sorted(xs)
    n = len(s)
    a = (1 - level) / 2
    lo = max((k for k in range(0, n + 1) if _binom_cdf(k - 1, n, q) <= a), default=None) if n else None
    hi = min((k for k in range(1, n + 1) if _binom_cdf(k - 1, n, q) >= 1 - a), default=None) if n else None
    if lo is None or hi is None or lo < 1:
        return None
    return s[lo - 1], s[hi - 1]


def summarise(latencies: list[float], wall: float | None) -> dict[str, Any]:
    from chimera.eval.proportions import wilson

    n = len(latencies)
    out: dict[str, Any] = {"n": n}
    if not n:
        return out
    for q in (0.50, 0.95, 0.99):
        out[f"p{int(q * 100)}"] = round(nearest_rank(latencies, q), 4)
        ci = quantile_interval(latencies, q)
        out[f"p{int(q * 100)}_ci"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
    for d in DEADLINES:
        hits = sum(x <= d for x in latencies)
        lo, hi = wilson(hits, n)
        out[f"phi_{d:g}"] = round(hits / n, 4)
        out[f"phi_{d:g}_wilson_nominal"] = [round(lo, 4), round(hi, 4)]
    out["mean"] = round(sum(latencies) / n, 4)
    out["max"] = round(max(latencies), 4)
    if wall:
        out["throughput"] = round(n / wall, 3)
    return out


def report(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    measured = [r for r in rows if r.get("kind") == "measured"]
    cells = {(r["sweep"], r["c"]): r for r in rows if r.get("kind") == "cell"}
    result: dict[str, Any] = {"rows": len(rows), "measured": len(measured)}
    if any(r.get("smoke") for r in measured):
        result["note"] = "SMOKE — checks the runner; not a reading"

    # A timeout is a censored latency, not a bad row; any other halt, an uncalibrated answer or
    # another build is the instrument failing.
    bad = [r for r in measured if not timed_out(r) and (r["halt"] or not r["calibrated"] or r["build"] != BUILD)]
    gpu = [r for r in rows if r.get("kind") == "gpu"]
    by_cell: dict[tuple[str, int], list[float]] = {}
    censored: dict[tuple[str, int], int] = {}
    for r in measured:
        key = (r["sweep"], r["c"])
        if timed_out(r):
            censored[key] = censored.get(key, 0) + 1
        elif r["halt"]:
            continue
        by_cell.setdefault(key, []).append(float(r["seconds"]))
    table = {}
    for (s, c), xs in sorted(by_cell.items()):
        table[f"{s}/c={c}"] = {**summarise(xs, (cells.get((s, c)) or {}).get("wall")), "timeouts": censored.get((s, c), 0)}
    result["cells"] = table

    expected = PASSES * 55 * len(LEVELS) * len(SWEEPS)
    p50_a = table.get("A/c=1", {}).get("p50")
    p50_b = table.get("B/c=1", {}).get("p50")
    drift_ok = bool(p50_a and p50_b) and abs(p50_a - p50_b) / min(p50_a, p50_b) <= DRIFT
    control = {
        "complete": len(measured) == expected, "bad_rows": len(bad),
        "gpu_idle_every_sweep": bool(gpu) and all(g.get("idle") for g in gpu),
        "c1_p50": [p50_a, p50_b], "drift_ok": drift_ok,
        # Amendment 2: how long each sweep waited for the GPU, and the temperature it started at.
        "gpu_waits": [{"sweep": g.get("sweep"), "waited_s": g.get("waited_s"), "temp_c": g.get("temp_c_last", g.get("temp_c"))} for g in gpu],
    }
    control["ok"] = control["complete"] and not bad and control["gpu_idle_every_sweep"] and drift_ok
    result["control"] = control
    if not control["ok"]:
        result["decision"] = "UNREADABLE: the registered control failed — the instrument moved, nothing is read"
        return result

    # Verdict stability (reported, not part of the decision): c=1 against c=8, pass 0, per sweep.
    stability = {}
    for s in ("A", "B"):
        # A timed-out call has no verdict to compare; it is counted in the cells, not here.
        v1 = {r["id"]: r["p"] >= TAU for r in measured
              if r["sweep"] == s and r["c"] == 1 and r["pass_"] == 0 and r["p"] is not None}
        v8 = {r["id"]: r["p"] >= TAU for r in measured
              if r["sweep"] == s and r["c"] == 8 and r["pass_"] == 0 and r["p"] is not None}
        stability[s] = sum(v1[i] != v8[i] for i in v1 if i in v8)
    result["verdict_flips_c1_vs_c8"] = stability

    lines = ["the deadline stays OFF by default"]
    for d in DEADLINES:
        meets = {c: [table[f"{s}/c={c}"][f"phi_{d:g}"] >= SUPPORTED_PHI for s in ("A", "B")] for c in LEVELS}
        supported = [c for c in LEVELS if all(meets[c])]
        # §15: a level the two sweeps disagree on is "not robust", never counted either way.
        unstable = [c for c in LEVELS if any(meets[c]) and not all(meets[c])]
        top = max((c for c in LEVELS if all(x in supported for x in LEVELS if x <= c)), default=None)
        if top is None:
            lines.append(f"a {d:g}s deadline is missed by more than 5% of calls even with one caller on this hardware")
        else:
            lines.append(f"supported concurrency at {d:g}s: c={top} (phi >= {SUPPORTED_PHI} in both sweeps)")
        if unstable:
            lines.append(f"not robust at {d:g}s (the sweeps disagree): c in {unstable}")
    shape = []
    for s in ("A", "B"):
        t1, t8 = table[f"{s}/c=1"].get("throughput"), table[f"{s}/c=8"].get("throughput")
        if not t1 or not t8:
            shape.append("unknown")
        elif abs(t8 / t1 - 1) <= SERIAL_BAND:
            shape.append("serialised")
        elif t8 / t1 >= BATCHED_AT:
            shape.append("batched")
        else:
            shape.append("not resolved")
    result["shape_by_sweep"] = shape
    lines.append(f"shape: {shape[0]}" if shape[0] == shape[1] else f"shape: not robust ({shape[0]} in A, {shape[1]} in B)")
    result["decision"] = "; ".join(lines)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--smoke", action="store_true")
    group.add_argument("--run", action="store_true")
    group.add_argument("--report", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--allow-busy-gpu", action="store_true",
                        help="smoke only: run on a GPU that is not idle; every row is marked busy_gpu")
    args = parser.parse_args()
    if args.check:
        rows = check()
        total = sum(len(order(rows, i, c)) for i, (_n, levels) in enumerate(SWEEPS) for c in levels)
        print(f"ok: {len(rows)} items, {total} measured calls + {WARMUP * len(SWEEPS)} warm-up, gpu {gpu_state()}")
        return
    if args.report:
        print(json.dumps(report(args.report), indent=2, ensure_ascii=False))
        return
    if args.out is None:
        parser.error("--out is required with --run and --smoke")
    if args.run and args.allow_busy_gpu:
        parser.error("--allow-busy-gpu is for the smoke only: the registered run needs an idle GPU")
    run(args.out, smoke=args.smoke, allow_busy_gpu=args.allow_busy_gpu)
    if args.smoke:
        print(json.dumps(report(args.out), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
