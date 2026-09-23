"""The pre-registered outcomes off ``results/{ctx,ship}.jsonl``.

    python -m bench.jevbench_local.read --jevbench PATH/TO/jevbench

ECE and Brier are JevBench's own functions (``jevbench.metrics``), imported from the pinned clone.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jev_decisions.report import wilson  # noqa: E402
from bench.jevbench_local.run import FILES, OUT_DIR, pinned  # noqa: E402

REFERENCES = {"Jev 1.13.0": 0.866, "SemIf/OpenJev (Qwen3.5-4B)": 0.810}


def rows_of(arm: str) -> list[dict[str, Any]]:
    path = OUT_DIR / f"{arm}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def acc_line(rows: list[dict[str, Any]]) -> str:
    k, n = sum(r["correct"] for r in rows), len(rows)
    if not n:
        return "n=0"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.3f} [{lo:.3f}, {hi:.3f}]"


def summary(arm: str, rows: list[dict[str, Any]], ece_top_label: Any, brier_score: Any) -> None:
    print(f"\n## arm {arm}  ({len(rows)} items)")
    print(f"accuracy, all 231 : {acc_line(rows)}")
    for name in FILES:
        print(f"  {name:<9}: {acc_line([r for r in rows if r['file'] == name])}")
    for kind in ("noul", "choice", "score"):
        print(f"  {kind:<9}: {acc_line([r for r in rows if r['type'] == kind])}")
    chance = sum(1 / len(r["labels"]) for r in rows) / max(len(rows), 1)
    print(f"chance baseline   : {chance:.3f}")
    invalid = [r for r in rows if not r["valid"]]
    print(f"no reading / invalid (counted wrong): {len(invalid)}")
    rescued = [r for r in invalid if r.get("choice") is not None and str(r["choice"]) == str(r["expected"])]
    print(f"  of which the WRITTEN label was right: {len(rescued)}  (first-token collision, study 21 A4 — exploratory)")
    valid = [r for r in rows if r["valid"] and r["confidence"] is not None]
    if valid:
        ece = ece_top_label([(r["confidence"], r["correct"]) for r in valid])["ece"]
        ece_hard = ece_top_label([(r["confidence"], r["correct"]) for r in valid if r["file"] == "hard"])["ece"]
        brier = sum(brier_score(r["shares"], str(r["expected"]), r["labels"]) for r in valid) / len(valid)
        print(f"top-label ECE (raw, valid items): {ece:.3f}   hard only: {ece_hard:.3f}")
        print(f"Brier (raw, valid items)        : {brier:.3f}")
    print(f"linter would refuse: {sum(bool(r['lint']) for r in rows)} of {len(rows)}")


def paired_hard(ctx: list[dict[str, Any]], ship: list[dict[str, Any]]) -> None:
    by_id = {r["id"]: r for r in ctx}
    pairs = [(by_id[r["id"]], r) for r in ship if r["file"] == "hard" and r["id"] in by_id]
    if not pairs:
        return
    n = len(pairs)
    a = sum(c["correct"] for c, _ in pairs) / n
    b = sum(s["correct"] for _, s in pairs) / n
    lost = sum(c["correct"] and not s["correct"] for c, s in pairs)
    won = sum(s["correct"] and not c["correct"] for c, s in pairs)
    cut = sum(s["prompt_eval_count"] < c["prompt_eval_count"] for c, s in pairs)
    print(f"\n## ship − ctx on hard (paired, n={n})")
    print(f"ctx {a:.3f}  ship {b:.3f}  Δ {100 * (b - a):+.1f} pp   (ship lost {lost}, won {won})")
    print(f"ship prompts shorter than ctx (truncated): {cut} of {n}")
    print("decision rule: context fix PR if ship loses >= 5 pp ->", "YES" if a - b >= 0.05 else "no")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jevbench", type=Path, required=True)
    args = parser.parse_args()
    clone = args.jevbench.resolve()
    pinned(clone)
    sys.path.insert(0, str(clone))
    from jevbench.metrics import brier_score, ece_top_label  # noqa: PLC0415

    ctx, ship = rows_of("ctx"), rows_of("ship")
    print("references (public accuracy, JevBench v1.4):", ", ".join(f"{k} {v:.3f}" for k, v in REFERENCES.items()))
    for arm, rows in (("ctx", ctx), ("ship", ship)):
        if rows:
            summary(arm, rows, ece_top_label, brier_score)
    paired_hard(ctx, ship)


if __name__ == "__main__":
    main()
