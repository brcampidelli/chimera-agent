"""Precision and recall of `chimera.fusion.grounded_question.is_question` on the labelled messages.

US$ 0, offline, deterministic. ``python -m bench.grounded_question_classifier.evaluate`` prints the
table per class and per language, and every miss. ``--check`` exits non-zero when precision on
"question" drops below 1.0 — the error that matters, because a task read as a question is a task
the verifier may decline.

What it cannot show: how often a real task is declined. The labels here say which messages are
questions; the cost of a misread is only measured by a paid run of the gate on tasks, and that run
has not been made (README.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from chimera.fusion.grounded_question import is_question

HERE = Path(__file__).resolve().parent
#: ``messages``: written by the classifier's author before the classifier (in-sample by author).
#: ``heldout``: written afterwards by a separate session that never saw the classifier.
SETS = {"messages": HERE / "messages.jsonl", "heldout": HERE / "heldout.jsonl"}


def load(name: str = "messages") -> list[dict[str, str]]:
    path = SETS[name]
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def scores(rows: list[dict[str, str]]) -> dict[str, dict[str, float | int | None]]:
    out: dict[str, dict[str, float | int | None]] = {}
    for label in ("question", "task"):
        tp = sum(1 for r in rows if r["pred"] == label and r["label"] == label)
        fp = sum(1 for r in rows if r["pred"] == label and r["label"] != label)
        fn = sum(1 for r in rows if r["pred"] != label and r["label"] == label)
        out[label] = {
            "n": sum(1 for r in rows if r["label"] == label),
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
        }
    return out


def _fmt(x: float | int | None) -> str:
    return "—" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--set", choices=sorted(SETS), default="messages")
    args = parser.parse_args()
    rows = load(args.set)
    for r in rows:
        r["pred"] = "question" if is_question(r["text"]) else "task"
    print(f"{'slice':6} {'class':9} {'n':>3} {'precision':>9} {'recall':>7}")
    worst = 1.0
    for lang in ("all", "pt", "en"):
        part = rows if lang == "all" else [r for r in rows if r["lang"] == lang]
        for label, s in scores(part).items():
            print(f"{lang:6} {label:9} {_fmt(s['n']):>3} {_fmt(s['precision']):>9} {_fmt(s['recall']):>7}")
            if label == "question" and s["precision"] is not None:
                worst = min(worst, float(s["precision"]))
    misses = [r for r in rows if r["pred"] != r["label"]]
    print(f"\n{len(misses)} misread of {len(rows)}:")
    for r in misses:
        print(f"  {r['id']:9} label={r['label']:8} read={r['pred']:8} {r['text']}")
    return 1 if args.check and worst < 1.0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
