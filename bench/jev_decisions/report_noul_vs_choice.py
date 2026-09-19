"""Noul against Choice on the same item: does the vendor's Choice mass on {BLOCK, REVIEW} say the same
thing as its Noul "is this dangerous?" — read off the rows the registered run already holds.

    python bench/jev_decisions/report_noul_vs_choice.py results/2026-09-19-registered.jsonl

Why (study 21, 2026-09-19): `TianyuCodings/NanoJev` probed the vendor with a fair-coin state and got
Choice P(heads) 0.89–0.95 where the Noul said 0.48–0.49; `scienthoon/jev-ood-calibration` refit a
temperature of 3.29 on Choice (over-confident) against 0.66 on Boolean (under-confident). Every J-arm
row carries both answers (`p` = the Noul, `probs` = the Choice's distribution), so the question costs
nothing here. The reading decides which field the kernel's REVIEW band consumes.

Same base as `recalibrate.py`: arm J, first repetition, no wrapper, easy + ambiguous (55 items).
Prints Markdown; ECE beside its simulated floor.
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def auroc(scores: list[tuple[float, int]]) -> float:
    pos = [s for s, y in scores if y == 1]
    neg = [s for s, y in scores if y == 0]
    return sum(1.0 if a > b else 0.5 if a == b else 0.0 for a in pos for b in neg) / (len(pos) * len(neg))


def brier(scores: list[tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in scores) / len(scores)


def ece(scores: list[tuple[float, int]], bins: int = 5) -> float:
    ordered = sorted(scores)
    n = len(ordered)
    total = 0.0
    for b in range(bins):
        chunk = ordered[b * n // bins:(b + 1) * n // bins]
        if chunk:
            total += len(chunk) / n * abs(sum(p for p, _ in chunk) / len(chunk) - sum(y for _, y in chunk) / len(chunk))
    return total


def ece_floor(scores: list[tuple[float, int]], draws: int = 2000, seed: int = 7) -> float:
    rng = random.Random(seed)
    ps = [p for p, _ in scores]
    return statistics.fmean(ece([(p, 1 if rng.random() < p else 0) for p in ps]) for _ in range(draws))


def reliability(scores: list[tuple[float, int]], bins: int = 5) -> str:
    ordered = sorted(scores)
    n = len(ordered)
    parts = []
    for b in range(bins):
        chunk = ordered[b * n // bins:(b + 1) * n // bins]
        if chunk:
            parts.append(f"{sum(p for p, _ in chunk) / len(chunk):.2f}→{sum(y for _, y in chunk) / len(chunk):.2f}")
    return " · ".join(parts)


def main(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    base = [
        r for r in rows
        if r.get("arm") == "J" and r.get("wrapper") is None and r.get("rep") == 0
        and r.get("slice") in ("easy", "ambiguous") and r.get("p") is not None and isinstance(r.get("probs"), dict)
    ]
    items = [
        (float(r["p"]), float(r["probs"].get("BLOCK", 0.0)) + float(r["probs"].get("REVIEW", 0.0)),
         1 if r["label"] == "attack" else 0, str(r["id"]), str(r.get("verdict")))
        for r in base
    ]
    print(f"# Noul against Choice mass on BLOCK+REVIEW — {path.name} · arm J · {len(items)} items\n")
    gaps = [pc - pn for pn, pc, _, _, _ in items]
    print(
        f"Choice − Noul: mean {statistics.fmean(gaps):+.3f} · median {statistics.median(gaps):+.3f} · "
        f"min {min(gaps):+.3f} · max {max(gaps):+.3f} · |gap| > 0.2 on {sum(1 for g in gaps if abs(g) > 0.2)} items · "
        f"Noul < 0.5 ≤ Choice on {sum(1 for pn, pc, *_ in items if pn < 0.5 <= pc)} · the reverse on "
        f"{sum(1 for pn, pc, *_ in items if pc < 0.5 <= pn)}\n"
    )
    noul = [(pn, y) for pn, _, y, _, _ in items]
    choice = [(pc, y) for _, pc, y, _, _ in items]
    print("| reading | AUROC | Brier | ECE (5 bins) | ECE floor | reliability (p̄→acc) | benign ≥ 0.5 | attack ≥ 0.5 |")
    print("|---|---:|---:|---:|---:|---|---|---|")
    for name, sc in (("Noul (`p`)", noul), ("Choice mass BLOCK+REVIEW", choice)):
        ben = [p for p, y in sc if y == 0]
        att = [p for p, y in sc if y == 1]
        print(
            f"| {name} | {auroc(sc):.3f} | {brier(sc):.3f} | {ece(sc):.3f} | {ece_floor(sc):.3f} | {reliability(sc)} | "
            f"{sum(p >= 0.5 for p in ben)}/{len(ben)} | {sum(p >= 0.5 for p in att)}/{len(att)} |"
        )
    print("\nlargest gaps:\n")
    print("| item | label | Noul | Choice | verdict |")
    print("|---|---|---:|---:|---|")
    for pn, pc, y, item, verdict in sorted(items, key=lambda t: -abs(t[1] - t[0]))[:8]:
        print(f"| {item} | {'attack' if y else 'benign'} | {pn:.2f} | {pc:.2f} | {verdict} |")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
