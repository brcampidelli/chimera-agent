"""Amendment 1 of PREREGISTRATION.md: clef-q4's JevBench rerun with the ubatch of its context.

Called by ``run_model.py --arm clef-q4 --clef-jevbench-rerun`` (see ``rerun_clef_jevbench.sh``).

1. The ub512 JevBench file is kept as ``jevbench.ub512.jsonl``. It is never read for a verdict.
2. Weight hashes are checked, the runner waits for an empty GPU, and the server starts with
   ``-b N -ub N``, N stepping down 8192 → 6144 → 4608 only when the server dies before it is healthy.
3. The raw smoke runs (a halt aborts), then JevBench-231 in full, then guards 4, 5 and 9.
4. Reuse check A: the 120 easy and original rows, all under 512 tokens, reproduce the ub512 rows (same
   argmax, |Δp| ≤ 0.001).
5. Reuse check B: the 55 unwrapped two-sided governance items, asked once more, reproduce repetition 0
   (same verdict, |Δp| ≤ 0.001).
6. If A or B fails, the governance files are kept as ``*.ub512.jsonl`` and registered + urgency4 are
   rerun in full under the new configuration.
7. ``rerun-amendment1.json`` records the ubatch used, both checks, durations and ``passed``.
"""

from __future__ import annotations

import argparse
import json
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx

from bench.jev_decisions import run as arm_j
from bench.local_decider_bakeoff import instruments
from bench.local_decider_bakeoff.common import (
    HERE,
    RESULTS,
    GuardError,
    gpu_alone,
    others_on_gpu,
    verify_hashes,
    vram_used,
)

TOL = 0.001
ARM = "clef-q4"


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check_jevbench_reuse(new: Path, old: Path) -> dict[str, Any]:
    """Reuse check A: easy and original rows (all under 512 tokens) must reproduce the ub512 run."""
    before = {r["id"]: r for r in _rows(old) if r["file"] in ("easy", "original")}
    after = {r["id"]: r for r in _rows(new) if r["file"] in ("easy", "original")}
    same_argmax = max_dp = 0.0
    n = 0
    for i, b in before.items():
        a = after.get(i)
        if not a or not a.get("probs") or not b.get("probs"):
            continue
        n += 1
        same_argmax += int(a["predicted"] == b["predicted"])
        max_dp = max(max_dp, max(abs(a["probs"][k] - b["probs"][k]) for k in b["probs"]))
    passed = n == 120 and same_argmax == 120 and max_dp <= TOL
    return {"n": n, "same_argmax": int(same_argmax), "max_abs_dp": max_dp, "passed": passed}


def check_governance_reuse(url: str, model: str, registered: Path) -> dict[str, Any]:
    """Reuse check B: the 55 unwrapped two-sided items under the new configuration against rep 0."""
    rep0 = {r["id"]: r for r in _rows(registered) if r.get("arm") == "J" and r.get("wrapper") is None and r.get("rep") == 0}
    call = instruments._local_jev(url, model)  # noqa: SLF001 — the same transport the run used
    same = n = 0
    max_dp = 0.0
    for item in arm_j.two_sided_items():
        old = rep0.get(item["id"])
        if old is None:
            continue
        new = call(httpx.Client(), item["state"])
        n += 1
        same += int(new["verdict"] == old["verdict"])
        max_dp = max(max_dp, abs(float(new["p"]) - float(old["p"])))
    passed = n == 55 and same == 55 and max_dp <= TOL
    return {"n": n, "same_verdict": same, "max_abs_dp": max_dp, "passed": passed}


def _start(stack: ExitStack, args: argparse.Namespace, logs: Path, meta: dict[str, Any]) -> str:
    from bench.local_decider_bakeoff.run_model import CLEF_UBATCH_LADDER, servers  # noqa: PLC0415

    for ubatch in CLEF_UBATCH_LADDER:
        procs, url = servers(ARM, args, logs, ubatch=ubatch)
        inner = ExitStack()
        try:
            for s in procs:
                inner.enter_context(s)
                s.wait_healthy()
        except GuardError as exc:
            inner.close()
            meta.setdefault("ubatch_failed", []).append({"ubatch": ubatch, "code": exc.code})
            if exc.code != 5:
                raise
            continue
        stack.push(inner)
        meta["ubatch"] = ubatch
        meta["own_pids"] = [s.proc.pid for s in procs if s.proc is not None]
        return url
    raise GuardError(f"clef-q4 did not load at any registered ubatch {CLEF_UBATCH_LADDER}", code=5)


def rerun(args: argparse.Namespace, spec: dict[str, Any]) -> None:
    from bench.local_decider_bakeoff.run_model import easy_guard, halt_guard  # noqa: PLC0415

    out = RESULTS / ARM
    jb, jb_old = out / "jevbench.jsonl", out / "jevbench.ub512.jsonl"
    if not jb_old.exists():
        if not jb.exists():
            raise GuardError("no ub512 JevBench file to keep", code=2)
        jb.rename(jb_old)  # a rerun that stopped midway resumes on jevbench.jsonl
    logs = args.bakeoff / "logs" / f"{ARM}-rerun"
    meta: dict[str, Any] = {"amendment": 1, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "passed": False}
    t0 = time.perf_counter()
    meta["hashes_checked"] = verify_hashes(HERE / "weights.sha256", args.bakeoff / "weights", spec["weights"])
    meta.update(gpu_alone(spec["min_free_mib"], args.gpu_wait))
    with ExitStack() as stack:
        url = _start(stack, args, logs, meta)
        meta["load_seconds"] = round(time.perf_counter() - t0, 1)
        meta["vram_used_mib_loaded"] = vram_used()
        meta["other_gpu_processes_after_load"] = others_on_gpu(set(meta["own_pids"]))
        print(f"[loaded] ubatch {meta['ubatch']} · {meta['load_seconds']}s · VRAM {meta['vram_used_mib_loaded']} MiB", flush=True)
        halt_guard(instruments.jevbench(url, jb, args.jevbench, send_labels=False, smoke=True), 3, "smoke")
        t = time.perf_counter()
        instruments.jevbench(url, jb, args.jevbench, send_labels=False)
        meta["jevbench_seconds"] = round(time.perf_counter() - t, 1)
        meta["jevbench"] = easy_guard(jb)
        halt_guard(meta["jevbench"]["halts"], 231, "jevbench")
        meta["reuse_jevbench_easy_original"] = check_jevbench_reuse(jb, jb_old)
        meta["reuse_governance_rep0"] = check_governance_reuse(url, spec["model"], out / "governance-registered.jsonl")
        print(f"[reuse] A {meta['reuse_jevbench_easy_original']} · B {meta['reuse_governance_rep0']}", flush=True)
        if not (meta["reuse_jevbench_easy_original"]["passed"] and meta["reuse_governance_rep0"]["passed"]):
            meta["governance_rerun"] = True
            for wrapper_set in ("registered", "urgency4"):
                path = out / f"governance-{wrapper_set}.jsonl"
                old = out / f"governance-{wrapper_set}.ub512.jsonl"
                if path.exists() and not old.exists():
                    path.rename(old)
                t = time.perf_counter()
                instruments.governance(url, spec["model"], path, wrapper_set)
                meta[f"governance_{wrapper_set}_seconds"] = round(time.perf_counter() - t, 1)
        meta["vram_used_mib_end"] = vram_used()
    meta["passed"] = True
    meta["total_seconds"] = round(time.perf_counter() - t0, 1)
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (out / "rerun-amendment1.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[done] clef-q4 JevBench rerun (Amendment 1) in {meta['total_seconds']}s", flush=True)
