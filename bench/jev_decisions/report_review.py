"""The registered metrics for the aacr-bench run (`PREREGISTRATION-review.md`).

    python bench/jev_decisions/report_review.py <rows.jsonl> [more.jsonl…]

Rows from one or more files (one arm per file is fine). `p` is P(correct comment); rejection is
`p < τ`. Recall of rejection = incorrect findings rejected; false rejection = correct findings
rejected — the two numbers `bench/review_judge` registered. Thresholds for the ROC points are chosen
on the 105 pilot rows and applied to the unseen rows. ECE beside its simulated floor; Brier and AUROC
on the same line, always.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fmt(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.2f} [{lo:.2f}, {hi:.2f}]" if n else "—"


def auroc(scores: list[tuple[float, int]]) -> float | None:
    """P(score_correct > score_incorrect): the label is 1 = correct, so a high p should go with 1."""
    pos = [s for s, y in scores if y == 1]
    neg = [s for s, y in scores if y == 0]
    if not pos or not neg:
        return None
    return sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg) / (len(pos) * len(neg))


def brier(scores: list[tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in scores) / len(scores)


def ece(scores: list[tuple[float, int]], bins: int = 10) -> float:
    ordered = sorted(scores)
    n = len(ordered)
    total = 0.0
    for b in range(bins):
        chunk = ordered[b * n // bins:(b + 1) * n // bins]
        if chunk:
            total += len(chunk) / n * abs(sum(p for p, _ in chunk) / len(chunk) - sum(y for _, y in chunk) / len(chunk))
    return total


def ece_floor(scores: list[tuple[float, int]], draws: int = 1000, seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    ps = [p for p, _ in scores]
    vals = sorted(ece([(p, 1 if rng.random() < p else 0) for p in ps]) for _ in range(draws))
    return statistics.fmean(vals), vals[int(0.95 * (len(vals) - 1))]


def reliability(scores: list[tuple[float, int]], bins: int = 10) -> str:
    ordered = sorted(scores)
    n = len(ordered)
    out = []
    for b in range(bins):
        chunk = ordered[b * n // bins:(b + 1) * n // bins]
        if chunk:
            out.append(f"{sum(p for p, _ in chunk) / len(chunk):.2f}→{sum(y for _, y in chunk) / len(chunk):.2f}")
    return " · ".join(out)


def rejection(scores: list[tuple[float, int]], tau: float) -> tuple[tuple[int, int], tuple[int, int]]:
    """((rejected incorrect, incorrect), (rejected correct, correct)) at p < tau."""
    inc = [p < tau for p, y in scores if y == 0]
    cor = [p < tau for p, y in scores if y == 1]
    return (sum(inc), len(inc)), (sum(cor), len(cor))


def best_tau(scores: list[tuple[float, int]], max_fr: float) -> float | None:
    """The τ (from the scores' own values) with the most rejections of incorrect findings while the
    false rejection stays ≤ max_fr."""
    best: tuple[int, float] | None = None
    for tau in sorted({p for p, _ in scores}, reverse=True):
        (ri, ni), (rc, nc) = rejection(scores, tau)
        if nc and rc / nc <= max_fr and (best is None or ri > best[0]):
            best = (ri, tau)
    return None if best is None else best[1]


def main(paths: list[Path]) -> None:
    rows: list[dict[str, Any]] = []
    metas: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                (metas if r.get("arm") == "meta" else rows).append(r)
    halts = [r for r in rows if r.get("halt")]
    rows = [r for r in rows if not r.get("halt")]
    print(f"# review — {', '.join(p.name for p in paths)}\n")
    print(f"rows {len(rows)} · halts {len(halts)} · spent {[m.get('spent') for m in metas]}\n")
    arms = sorted({r["arm"] for r in rows})
    for arm in arms:
        sub = [r for r in rows if r["arm"] == arm]
        withp = [r for r in sub if r.get("p") is not None]
        print(f"## arm {arm} — {len(sub)} rows, {len(withp)} with a probability, {len(sub) - len(withp)} without\n")
        pilot = [(float(r["p"]), int(r["label"])) for r in withp if r["in_pilot"]]
        unseen = [(float(r["p"]), int(r["label"])) for r in withp if not r["in_pilot"]]
        every = pilot + unseen
        print("| rows | n | AUROC | Brier | ECE (10) | ECE floor mean / p95 | recall@0.5 | FR@0.5 |")
        print("|---|---:|---:|---:|---:|---|---|---|")
        for name, sc in (("unseen", unseen), ("pilot", pilot), ("all", every)):
            if not sc:
                continue
            au = auroc(sc)
            fl = ece_floor(sc) if len(sc) >= 30 else (float("nan"), float("nan"))
            (ri, ni), (rc, nc) = rejection(sc, 0.5)
            print(f"| {name} | {len(sc)} | {au:.3f} | {brier(sc):.3f} | {ece(sc):.3f} | {fl[0]:.3f} / {fl[1]:.3f} | {fmt(ri, ni)} | {fmt(rc, nc)} |" if au is not None else f"| {name} | {len(sc)} | — |")
        if every:
            print(f"\nreliability (all, 10 equal-mass bins, p̄→acc): {reliability(every)}\n")
        # ROC points: τ on the pilot, applied to the unseen
        if pilot and unseen:
            print("| bound | τ (from pilot) | unseen: recall of rejection | unseen: false rejection | pilot: recall | pilot: FR |")
            print("|---|---:|---|---|---|---|")
            for max_fr in (0.10, 0.20):
                tau = best_tau(pilot, max_fr)
                if tau is None:
                    print(f"| FR ≤ {max_fr:.2f} | — | no τ on the pilot | | | |")
                    continue
                (ri, ni), (rc, nc) = rejection(unseen, tau)
                (pri, pni), (prc, pnc) = rejection(pilot, tau)
                print(f"| FR ≤ {max_fr:.2f} | {tau:.2f} | {fmt(ri, ni)} | {fmt(rc, nc)} | {fmt(pri, pni)} | {fmt(prc, pnc)} |")
            print()
        # the verdict, for the pair with arms A (15.1 / 0.0) and C (60.4 / 38.5) on the pilot rows
        vp = [r for r in sub if r["in_pilot"] and r.get("verdict")]
        if vp:
            ri = sum(1 for r in vp if r["label"] == 0 and r["verdict"] == "reject")
            ni = sum(1 for r in vp if r["label"] == 0)
            rc = sum(1 for r in vp if r["label"] == 1 and r["verdict"] == "reject")
            nc = sum(1 for r in vp if r["label"] == 1)
            print(f"verdict on the pilot rows: recall of rejection {fmt(ri, ni)} · false rejection {fmt(rc, nc)} (arm A 8/53 · 0/52; arm C 32/53 · 20/52)\n")
        vu = [r for r in sub if not r["in_pilot"] and r.get("verdict")]
        if vu:
            ri = sum(1 for r in vu if r["label"] == 0 and r["verdict"] == "reject")
            ni = sum(1 for r in vu if r["label"] == 0)
            rc = sum(1 for r in vu if r["label"] == 1 and r["verdict"] == "reject")
            nc = sum(1 for r in vu if r["label"] == 1)
            print(f"verdict on the unseen rows: recall of rejection {fmt(ri, ni)} · false rejection {fmt(rc, nc)}\n")
        # by source model, and within-repo AUROC against pooled
        print("| source model | n (incorrect / correct) | AUROC |")
        print("|---|---|---:|")
        by_model: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for r in withp:
            by_model[r["source_model"]].append((float(r["p"]), int(r["label"])))
        for model, sc in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
            au = auroc(sc)
            print(f"| {model} | {len(sc)} ({sum(1 for _, y in sc if y == 0)} / {sum(1 for _, y in sc if y == 1)}) | {au:.3f} |" if au is not None else f"| {model} | {len(sc)} | — |")
        by_repo: dict[str, list[tuple[float, int]]] = defaultdict(list)
        for r in withp:
            by_repo[r["repo"]].append((float(r["p"]), int(r["label"])))
        within = [(auroc(sc), len(sc)) for sc in by_repo.values() if auroc(sc) is not None]
        if within:
            w = sum(a * n for a, n in within) / sum(n for _, n in within)  # type: ignore[misc]
            print(f"\nwithin-repository AUROC (weighted over {len(within)} repositories with both labels): {w:.3f} — against pooled {auroc(every):.3f}\n")
        secs = sorted(float(r["seconds"]) for r in sub if r.get("seconds") is not None)
        usd = sum(float(r["usd"]) for r in sub if r.get("usd"))
        toks = [int(r["in_tokens"]) for r in sub if r.get("in_tokens")]
        if secs:
            print(f"cost US$ {usd:.4f} total · US$ {usd / len(sub):.6f} per request · latency p50 {secs[len(secs) // 2]:.2f} s, p95 {secs[int(0.95 * (len(secs) - 1))]:.2f} s · input tokens median {statistics.median(toks) if toks else '—'}\n")


if __name__ == "__main__":
    main([Path(a) for a in sys.argv[1:]])
