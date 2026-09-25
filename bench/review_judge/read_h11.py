"""H11 restated on verdicts already on disk — precision of the kept set, recall of correct comments.

    python bench/review_judge/read_h11.py

Study 25's H11 asks whether a review rubric raises PRECISION without collapsing RECALL. For a filter
over fixed comments those are: the share of kept comments that are correct, and the share of correct
comments kept (1 − false rejection). The arms in this directory were read as rejection recall and
false rejection; this prints the same verdicts in H11's terms. See NOTE-h11.md.

Not pre-registered: a re-expression of published verdicts, no model calls. Stdlib only. The Wilson
interval is `chimera/eval/anytime.py`'s; the paired bootstrap resamples within label, seed and draws
as in `read_full.py`.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parent / "results"
Z95 = 1.959963984540054
SEED = 20260819
DRAWS = 10_000
PI_SLICE = 754 / 1017  # Diff Level prevalence of correct comments, PREREGISTRATION.md
#: RESULTS.md's published pilot counts for A and C, whose details the full run overwrote in place.
PILOT_PUBLISHED = {"A cautious (pilot)": (8, 53, 0, 52), "C split (pilot)": (32, 53, 20, 52)}


def load(arm: str) -> list[dict[str, Any]]:
    text = (RESULTS / arm / "details.jsonl").read_text(encoding="utf-8")
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def wilson(k: int, n: int) -> tuple[float, float]:
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + Z95 * Z95 / n
    c = (p + Z95 * Z95 / (2 * n)) / d
    m = (Z95 / d) * math.sqrt(p * (1 - p) / n + Z95 * Z95 / (4 * n * n))
    return (max(0.0, c - m), min(1.0, c + m))


def counts(rows: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    """(bad, good, bad kept, good kept) over graded rows."""
    graded = [r for r in rows if r["verdict"] in ("approve", "reject")]
    bad = [r for r in graded if r["label"] == 0]
    good = [r for r in graded if r["label"] == 1]
    return (len(bad), len(good), sum(r["verdict"] == "approve" for r in bad),
            sum(r["verdict"] == "approve" for r in good))


def reweighted(tpr: float, keep: float) -> float:
    """Precision of the kept set at the slice's prevalence, for the balanced pilot arms."""
    kept_good = keep * PI_SLICE
    return kept_good / (kept_good + (1 - tpr) * (1 - PI_SLICE))


def show(name: str, rows: list[dict[str, Any]], *, balanced: bool = False) -> None:
    nb, ng, kb, kg = counts(rows)
    tpr, keep, prec = (nb - kb) / nb, kg / ng, kg / (kg + kb)
    klo, khi = wilson(kg, ng)
    plo, phi = wilson(kg, kg + kb)
    extra = f"  precision@74.1% {reweighted(tpr, keep):.1%}" if balanced else ""
    print(f"  {name:<34} n={nb + ng:<4} catches bad {tpr:6.1%}   keeps correct {keep:6.1%} "
          f"[{klo:.1%}, {khi:.1%}]   precision {prec:6.1%} [{plo:.1%}, {phi:.1%}]   "
          f"keep-all {ng / (ng + nb):.1%}{extra}")


def paired_precision(a_rows: list[dict[str, Any]], c_rows: list[dict[str, Any]]
                     ) -> tuple[float, float, float]:
    """C − A in precision of the kept set, paired bootstrap over items within label."""
    pairs = [(a["label"], a["verdict"] == "approve", c["verdict"] == "approve")
             for a, c in zip(a_rows, c_rows, strict=True)
             if a["verdict"] in ("approve", "reject") and c["verdict"] in ("approve", "reject")]
    bad = [p for p in pairs if p[0] == 0]
    good = [p for p in pairs if p[0] == 1]

    def delta(b: list[tuple[int, bool, bool]], g: list[tuple[int, bool, bool]]) -> float:
        def prec(i: int) -> float:
            kept_good = sum(p[i] for p in g)
            return kept_good / (kept_good + sum(p[i] for p in b))
        return prec(2) - prec(1)

    rng = random.Random(SEED)
    draws = sorted(delta(rng.choices(bad, k=len(bad)), rng.choices(good, k=len(good)))
                   for _ in range(DRAWS))
    return delta(bad, good), draws[int(0.025 * DRAWS)], draws[int(0.975 * DRAWS) - 1]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    a, c = load("cautious"), load("split")
    subsets = {"all": lambda r: True, "in-sample": lambda r: r["in_pilot"],
               "out-of-sample": lambda r: not r["in_pilot"]}
    for name, pick in subsets.items():
        print(f"== full-slice run, {name}")
        show("A cautious", [r for r in a if pick(r)])
        show("C split (introduced + no-defect)", [r for r in c if pick(r)])
    point, lo, hi = paired_precision([r for r in a if not r["in_pilot"]],
                                     [r for r in c if not r["in_pilot"]])
    print(f"\n  out of sample, C − A in precision {point * 100:+.1f} pp, paired bootstrap "
          f"[{lo * 100:+.1f}, {hi * 100:+.1f}]")

    print("\n== the pilot's 105 (balanced 53/52; precision also reweighted to the slice's 74.1%)")
    for label, (caught, bad, wrongly, good) in PILOT_PUBLISHED.items():
        tpr, keep = caught / bad, 1 - wrongly / good
        print(f"  {label:<34} catches bad {tpr:6.1%}   keeps correct {keep:6.1%}   "
              f"precision@74.1% {reweighted(tpr, keep):.1%}   (published counts)")
    for arm, label in (("neutral", "B neutral"), ("preexisting", "E introduced-only"),
                       ("nodefect", "D no-defect-only")):
        show(label, load(arm), balanced=True)


if __name__ == "__main__":
    main()
