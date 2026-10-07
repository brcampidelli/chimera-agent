"""The registered JevBench metrics for Intern-Decision-4B — see PREREGISTRATION.md.

    python bench/intern_decision_local/report_jevbench.py [--readout PATH]

Reads only; prints Markdown. Accuracy per tier with Wilson intervals, against the vendor's claim;
ECE and Brier raw (T = 1) and under the vendor's 4B temperature preset (borrowed: it was fitted on
the XTuner backend); and paired exact McNemar against our two local readings on the same items —
the September shipped run (``bench/jevbench_local/results/ship.jsonl``) and the S30-54 digit-id arm
(``bench/decision_readout/results.jsonl``, rows ``dataset=jevbench, arm=C_numeric``).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
T_PRESET = 1.9924182353655278  # vendor benchmarks/temperature-presets.json, intern-decision-4b
VENDOR = {"easy": 1.0, "original": 0.9861, "hard": 0.7387}
TIERS = ("easy", "original", "hard")

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (0.0 if k == 0 else max(0.0, c - h), 1.0 if k == n else min(1.0, c + h))


def rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.3f} [{lo:.3f}, {hi:.3f}]"


def scaled(probs: dict[str, float], t: float) -> dict[str, float]:
    logs = {k: (math.log(v) if v > 0 else -math.inf) for k, v in probs.items()}
    top = max(logs.values())
    w = {k: math.exp((v - top) / t) for k, v in logs.items()}
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def top(probs: dict[str, float]) -> str:
    return min(probs, key=lambda k: (-probs[k], k))


def ece_brier(rows: list[dict[str, Any]], t: float, bins: int = 10) -> tuple[float, float]:
    pairs, briers = [], []
    for r in rows:
        if not r.get("probs"):
            continue
        p = scaled(r["probs"], t)
        if top(p) != top(r["probs"]):
            raise ArithmeticError(f"{r['id']}: temperature changed the argmax")
        pairs.append((max(p.values()), bool(r["correct"])))
        briers.append(sum((v - (1.0 if k == r["expected"] else 0.0)) ** 2 for k, v in p.items()))
    total = 0.0
    for b in range(bins):
        cell = [(c, y) for c, y in pairs if (b / bins < c <= (b + 1) / bins) or (b == 0 and c == 0)]
        if cell:
            total += (
                len(cell)
                / len(pairs)
                * abs(statistics.fmean(c for c, _ in cell) - statistics.fmean(y for _, y in cell))
            )
    return total, statistics.fmean(briers)


def mcnemar(ours: dict[str, bool], ref: dict[str, bool]) -> str:
    ids = sorted(set(ours) & set(ref))
    b = sum(ours[i] and not ref[i] for i in ids)  # Intern right, reference wrong
    c = sum(ref[i] and not ours[i] for i in ids)
    n, k = b + c, min(b, c)
    p = min(1.0, 2 * sum(math.comb(n, j) for j in range(k + 1)) / 2**n) if n else 1.0
    return f"n = {len(ids)} · Intern-only {b} / reference-only {c} · exact p = {p:.2g}"


def load(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--readout", type=Path, default=ROOT / "bench" / "decision_readout" / "results.jsonl"
    )
    args = ap.parse_args()
    rows = load(HERE / "results" / "jevbench.jsonl")
    if len({r["id"] for r in rows}) != 231:
        raise SystemExit(f"{len(rows)} rows; the registration is 231 items")
    halts = [r for r in rows if r.get("halt")]

    print("## JevBench, 231 items (unread or failed = wrong)\n")
    print(
        "| tier | Intern-Decision-4B (ours, HF + offload) | vendor (XTuner) | inside our interval? |"
    )
    print("|---|---|---:|---|")
    for tier in (*TIERS, "all"):
        sub = [r for r in rows if tier == "all" or r["file"] == tier]
        k = sum(bool(r["correct"]) for r in sub)
        lo, hi = wilson(k, len(sub))
        v = VENDOR.get(tier)
        print(
            f"| {tier} | {rate(k, len(sub))} | {v if v is not None else '—'} | {('yes' if lo <= v <= hi else 'no') if v is not None else '—'} |"
        )
    print(
        f"\nHalts: {len(halts)}. Control (easy ≥ 0.90): "
        f"{'met' if sum(r['correct'] for r in rows if r['file'] == 'easy') / 48 >= 0.90 else 'NOT MET — do not read the rest'}.\n"
    )

    print("## Calibration (top-label ECE, 10 bins; multiclass Brier)\n")
    print("| reading | tier | ECE | Brier |")
    print("|---|---|---:|---:|")
    for label, t in (
        ("raw, T = 1", 1.0),
        (f"vendor preset T = {T_PRESET:.4f} (borrowed, XTuner-fitted)", T_PRESET),
    ):
        for tier in ("hard", "all"):
            sub = [r for r in rows if tier == "all" or r["file"] == tier]
            e, b = ece_brier(sub, t)
            print(f"| {label} | {tier} | {e:.3f} | {b:.3f} |")
    print()

    print("## Paired against our local decider (same 231 items)\n")
    mine = {r["id"]: bool(r["correct"]) for r in rows}
    refs: list[tuple[str, dict[str, bool]]] = []
    ship = ROOT / "bench" / "jevbench_local" / "results" / "ship.jsonl"
    refs.append(
        (
            "qwen3:4b shipped rendering (2026-09, `jevbench_local`)",
            {r["id"]: bool(r["correct"]) for r in load(ship)},
        )
    )
    if args.readout.exists():
        cn = {
            r["id"]: bool(r["correct"])
            for r in load(args.readout)
            if r.get("dataset") == "jevbench" and r.get("arm") == "C_numeric"
        }
        refs.append(("qwen3:4b digit ids (S30-54 `C_numeric`)", cn))
    else:
        print(f"(S30-54 rows not found at {args.readout}; that comparison is skipped)\n")
    for name, ref in refs:
        print(
            f"**vs {name}** — reference {sum(ref.values())}/{len(ref)} = {sum(ref.values()) / len(ref):.3f}"
        )
        for tier in TIERS:
            ids = {r["id"] for r in rows if r["file"] == tier}
            print(
                f"- {tier}: Intern {sum(mine[i] for i in ids & set(ref))} vs reference {sum(ref[i] for i in ids & set(ref))} · {mcnemar({i: mine[i] for i in ids}, {i: ref[i] for i in ids if i in ref})}"
            )
        print(f"- all: {mcnemar(mine, ref)}\n")

    ms = sorted(float(r["ms"]) for r in rows if r.get("ms") is not None)
    toks = [int(r["input_tokens"]) for r in rows if r.get("input_tokens")]
    print(
        f"Latency (server forward, this laptop with CPU offload — not the model's speed): p50 {ms[len(ms) // 2]:.0f} ms, "
        f"p95 {ms[int(0.95 * (len(ms) - 1))]:.0f} ms · input tokens median {statistics.median(toks):.0f}, max {max(toks)}"
    )


if __name__ == "__main__":
    main()
