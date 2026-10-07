"""Intern-Decision-4B through the arm J governance design — see PREREGISTRATION.md (written first).

    python -m bench.intern_decision_local.run_governance --wrapper-set registered [--smoke]
    python -m bench.intern_decision_local.run_governance --wrapper-set urgency4

Everything but the transport is ``bench/jev_decisions/run.py``: the same two questions, items, order,
repetitions, wrappers and OATS cases, and the same row fields, so ``bench/jev_decisions/report.py``
reads the output unchanged. ``run.jev`` is swapped for a call to the local sidecar (``server.py``);
the row's ``model`` names Intern, ``usd`` is 0. One worker: the sidecar serves one forward at a time.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jev_decisions import run as arm_j  # noqa: E402

URL = "http://127.0.0.1:8765/v1/decisions"
MODEL = "internlm/Intern-Decision-4B@0e5e6aa"
OUT_DIR = Path(__file__).resolve().parent / "results"
_local = httpx.Client(timeout=600.0)


def intern(
    _client: httpx.Client, state: str, questions: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Same return shape as ``run.jev``; the request goes to the sidecar instead of OpenRouter."""
    t0 = time.perf_counter()
    r = _local.post(URL, json={"state": state, "questions": questions or arm_j.QUESTIONS})
    r.raise_for_status()
    data = r.json()
    danger = data["answers"]["danger"]
    verdict = data["answers"]["verdict"]
    return {
        "p": danger.get("noul"),
        "verdict": verdict.get("choice"),
        "probs": verdict.get("probabilities"),
        "confidence": verdict.get("confidence"),
        "usd": 0.0,
        "in_tokens": data["usage"]["input_tokens"],
        "out_tokens": data["usage"]["output_tokens"],
        "provider": "local-sidecar",
        "model": MODEL,
        "seconds": round(time.perf_counter() - t0, 3),
        "raw": {"danger": danger, "verdict": verdict, "server_ms": data["timing"].get("server_ms")},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wrapper-set", default="registered", choices=("registered", "urgency4"))
    ap.add_argument(
        "--smoke", action="store_true", help="3 governance items, printed raw, nothing written"
    )
    args = ap.parse_args()
    arm_j.jev = intern  # the only change to the arm J design
    arm_j.JEV_MODEL = MODEL
    if args.smoke:
        for item in arm_j.two_sided_items()[:2] + arm_j.oats_items()[:1]:
            res = intern(_local, item["state"])
            print(
                item["id"],
                item.get("label"),
                "| p_danger",
                round(res["p"], 4),
                "| verdict",
                res["verdict"],
                {k: round(v, 4) for k, v in res["probs"].items()},
                f"| {res['seconds']}s",
            )
        return
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"governance-{args.wrapper_set}.jsonl"
    arm_j.run(
        out, httpx.Client(), None, reps_j=5, workers=1, arms="J", wrapper_set=args.wrapper_set
    )


if __name__ == "__main__":
    main()
