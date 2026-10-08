"""One arm of the local bake-off, end to end — see PREREGISTRATION.md (written first) and RUN.md.

    python bench/local_decider_bakeoff/run_model.py --arm clef-q4 --bakeoff DIR --jevbench CLONE \\
        --llama-dir DIR --intern-python PY --intern-vendor DIR [--smoke]

Verifies the weight hashes (guard 1), waits for the GPU to be free and refuses to measure beside
another process (guard 2), starts the arm's own server(s), waits for health, prints the raw smoke
(guard 3), runs JevBench-231, checks easy ≥ 0.90 (guard 5), runs governance ``registered`` and
``urgency4``, stops its servers, and — for clef-q4 — runs the hosted control (needs no GPU). Every
answer passes guard 4. Exit 0 only when every guard passed; a rerun resumes where it stopped.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.local_decider_bakeoff import instruments  # noqa: E402
from bench.local_decider_bakeoff.common import (  # noqa: E402
    HERE,
    RESULTS,
    GuardError,
    Server,
    gpu_alone,
    others_on_gpu,
    verify_hashes,
    vram_used,
)

MAIN_CHECKOUT = Path(r"C:\Users\brcam\Desktop\Desenvolvendo Projetos\Agent AI")
HOSTED_CLEF = "cloudflare/clef-flash"
LLAMA_FLAGS = ["-ngl", "99", "-c", "8192", "-np", "1", "--host", "127.0.0.1"]

ARMS: dict[str, dict[str, Any]] = {
    "clef-q4": {"weights": "clef-flash-gguf/", "min_free_mib": 7000, "send_labels": False,
                "model": "bartowski/Cloudflare_clef-flash-GGUF@5fcdd9b Q4_K_M"},
    "intern-2b": {"weights": "intern-decision-2b/", "min_free_mib": 5500, "send_labels": False,
                  "model": "internlm/Intern-Decision-2B@8797836"},
    "eikos-4b": {"weights": "eikos-4b", "min_free_mib": 5500, "send_labels": True,
                 "model": "caiovicentino1/Eikos-4B@d06420b GGUF Q8_0"},
}


def servers(arm: str, args: argparse.Namespace, logs: Path) -> tuple[list[Server], str]:
    """The arm's server processes, in start order, and the decision URL the instruments call."""
    weights = args.bakeoff / "weights"
    exe = str(args.llama_dir / "llama-server.exe")
    if arm == "clef-q4":
        gguf = weights / "clef-flash-gguf" / "Cloudflare_clef-flash-Q4_K_M.gguf"
        s = Server([exe, "-m", str(gguf), *LLAMA_FLAGS, "--port", "8090"], "http://127.0.0.1:8090/health", logs / "llama-server.log")
        return [s], "http://127.0.0.1:8090/v1/systemone"
    if arm == "intern-2b":
        script = HERE / "intern_server.py"
        s = Server([str(args.intern_python), str(script), "--vendor", str(args.intern_vendor),
                    "--checkpoint", str(weights / "intern-decision-2b"), "--port", "8765"],
                   "http://127.0.0.1:8765/health", logs / "intern-server.log")
        return [s], "http://127.0.0.1:8765/v1/decisions"
    gguf = weights / "eikos-4b-gguf" / "Eikos-4B-Q8_0.gguf"
    llama = Server([exe, "-m", str(gguf), *LLAMA_FLAGS, "--port", "8091"], "http://127.0.0.1:8091/health", logs / "llama-server.log")
    side = Server([str(args.intern_python), str(HERE / "eikos_server.py"), "--checkpoint", str(weights / "eikos-4b"),
                   "--llama", "http://127.0.0.1:8091", "--port", "8766"], "http://127.0.0.1:8766/health", logs / "eikos-server.log")
    return [llama, side], "http://127.0.0.1:8766/v1/systemone"


def easy_guard(path: Path) -> dict[str, Any]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    per = {t: [r for r in rows if r["file"] == t] for t in ("easy", "original", "hard")}
    acc = {t: (sum(r["correct"] for r in rs) / len(rs) if rs else None) for t, rs in per.items()}
    counts = {t: len(rs) for t, rs in per.items()}
    print(f"[jevbench] accuracy {acc} (n {counts})", flush=True)
    if counts != {"easy": 48, "original": 72, "hard": 111}:
        raise GuardError(f"JevBench incomplete: {counts}", code=6)
    if (acc["easy"] or 0.0) < 0.90:
        raise GuardError(f"guard 5: JevBench easy {acc['easy']:.3f} < 0.90", code=6)
    return {"accuracy": acc, "n": counts}


def load_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    env = MAIN_CHECKOUT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("OPENROUTER_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    return key


def hosted_control(out: Path) -> str:
    """Control C: the same governance design through the hosted Clef, the same day. ≈ US$ 0.02."""
    if out.exists() and any('"arm": "meta"' in line for line in out.read_text(encoding="utf-8").splitlines()):
        return "already done"
    key = load_key()
    if not key:
        return "no key — the report falls back to the published 2026-10-06 hosted rows"
    env = {**os.environ, "OPENROUTER_API_KEY": key, "PYTHONUTF8": "1"}
    cmd = [sys.executable, str(ROOT / "bench" / "jev_decisions" / "run.py"), "--run", "--arms", "J", "--workers", "8",
           "--decision-model", HOSTED_CLEF, "--wrapper-set", "registered", "--out", str(out)]
    done = subprocess.run(cmd, env=env, cwd=str(ROOT), check=False)
    return f"exit {done.returncode}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arm", choices=sorted(ARMS), required=True)
    ap.add_argument("--bakeoff", type=Path, required=True, help="weights and logs, outside the repository")
    ap.add_argument("--jevbench", type=Path, required=True)
    ap.add_argument("--llama-dir", type=Path, required=True)
    ap.add_argument("--intern-python", type=Path, required=True)
    ap.add_argument("--intern-vendor", type=Path, required=True)
    ap.add_argument("--smoke", action="store_true", help="guards 1–3 and the raw smoke only; nothing written")
    ap.add_argument("--gpu-wait", type=float, default=900.0)
    args = ap.parse_args()
    spec = ARMS[args.arm]
    out_dir = RESULTS / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)
    logs = args.bakeoff / "logs" / args.arm
    meta: dict[str, Any] = {"arm": args.arm, "model": spec["model"], "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "llama_flags": LLAMA_FLAGS, "smoke": args.smoke}
    t_start = time.perf_counter()

    meta["hashes_checked"] = verify_hashes(HERE / "weights.sha256", args.bakeoff / "weights", spec["weights"])
    meta.update(gpu_alone(spec["min_free_mib"], args.gpu_wait))
    procs, url = servers(args.arm, args, logs)
    with ExitStack() as stack:
        for s in procs:
            stack.enter_context(s)
            health = s.wait_healthy()
            meta.setdefault("health", []).append(health)
        meta["load_seconds"] = round(time.perf_counter() - t_start, 1)
        meta["vram_used_mib_loaded"] = vram_used()
        own = {s.proc.pid for s in procs if s.proc is not None}
        meta["other_gpu_processes_after_load"] = others_on_gpu(own)
        print(f"[loaded] {meta['load_seconds']}s · VRAM {meta['vram_used_mib_loaded']} MiB · others {meta['other_gpu_processes_after_load']}", flush=True)

        print("[smoke] 3 JevBench items, raw", flush=True)
        instruments.jevbench(url, out_dir / "jevbench.jsonl", args.jevbench, send_labels=spec["send_labels"], smoke=True)
        print("[smoke] 3 governance items, raw", flush=True)
        instruments.governance(url, spec["model"], out_dir / "governance-registered.jsonl", "registered", smoke=True)
        if args.smoke:
            print(json.dumps(meta, ensure_ascii=False))
            return

        t = time.perf_counter()
        instruments.jevbench(url, out_dir / "jevbench.jsonl", args.jevbench, send_labels=spec["send_labels"])
        meta["jevbench_seconds"] = round(time.perf_counter() - t, 1)
        meta["jevbench"] = easy_guard(out_dir / "jevbench.jsonl")
        for wrapper_set in ("registered", "urgency4"):
            t = time.perf_counter()
            path = out_dir / f"governance-{wrapper_set}.jsonl"
            if not (path.exists() and '"arm": "meta"' in path.read_text(encoding="utf-8")):
                instruments.governance(url, spec["model"], path, wrapper_set)
            meta[f"governance_{wrapper_set}_seconds"] = round(time.perf_counter() - t, 1)
        meta["vram_used_mib_end"] = vram_used()
    if args.arm == "clef-q4":
        meta["hosted_control"] = hosted_control(out_dir / "hosted-registered.jsonl")
    meta["total_seconds"] = round(time.perf_counter() - t_start, 1)
    meta["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    (out_dir / "run-meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"[done] {args.arm} in {meta['total_seconds']}s", flush=True)


if __name__ == "__main__":
    main()
