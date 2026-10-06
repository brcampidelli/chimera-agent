"""Run the opt-in local System One retrieval comparison registered in PREREGISTRATION.md."""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Any

from bench.memory_selection.items import ITEMS
from chimera.config import get_settings
from chimera.decisions.contract import Decider
from chimera.decisions.local import DEFAULT_MODEL, LocalLogprobBackend
from chimera.memory.history import HistoryIndex
from chimera.tools.history import rerank_history_hits


def run(db: Path, *, model: str, k: int) -> dict[str, Any]:
    settings = get_settings()
    decider = Decider(LocalLogprobBackend(settings.ollama_base_url, model))
    index = HistoryIndex(db.parent)
    arms: dict[str, list[str]] = {"fts": [], "rerank": []}
    rows: list[dict[str, Any]] = []
    seconds = 0.0
    try:
        for item in ITEMS:
            candidates = index.search(item.query, project=f"synthetic-memory-selection/{item.id}", k=30)
            start = time.perf_counter()
            halts: list[str] = []
            ranked = rerank_history_hits(item.query, candidates, decider, limit=k, halts=halts)
            seconds += time.perf_counter() - start
            def relevant(hits: list[Any], answer: str = item.answer) -> bool:
                return answer.casefold() in " ".join(hit.answered for hit in hits).casefold()
            fts_top = candidates[:k]
            selected_top = ranked[:k]
            arms["fts"].append("correct" if relevant(fts_top) else "incorrect")
            arms["rerank"].append("correct" if relevant(selected_top) else "incorrect")
            reciprocal_rank = next(
                (1.0 / rank for rank, hit in enumerate(ranked, 1) if item.answer.casefold() in hit.answered.casefold()),
                0.0,
            )
            rows.append({
                "id": item.id,
                "relevant_turn": item.relevant_turn,
                "fts_rank": next((i for i, hit in enumerate(candidates, 1) if hit.turn_id == item.relevant_turn), None),
                "rerank_rank": next((i for i, hit in enumerate(ranked, 1) if hit.turn_id == item.relevant_turn), None),
                "fts_correct_at_k": relevant(fts_top),
                "rerank_correct_at_k": relevant(selected_top),
                "rerank_reciprocal_rank": reciprocal_rank,
                "halts": halts,
            })
    finally:
        index.close()
    # A halted item falls back to FTS order, so it scores exactly like arm A and pulls the paired
    # difference toward 0, i.e. toward "non-inferior". An unavailable or unreadable model is an
    # incomplete run (registered), never a pass: no bound is computed over halted items.
    halted = [row["id"] for row in rows if row["halts"]]
    successes = sum(row["rerank_correct_at_k"] for row in rows)
    base = sum(row["fts_correct_at_k"] for row in rows)
    rng = random.Random(3055)
    differences = [int(row["rerank_correct_at_k"]) - int(row["fts_correct_at_k"]) for row in rows]
    samples = sorted(
        sum(rng.choice(differences) for _ in differences) / len(differences)
        for _ in range(10_000)
    )
    return {
        "model": model,
        "k": k,
        "items": len(ITEMS),
        "fts_accuracy": base / len(rows),
        "rerank_accuracy": successes / len(rows),
        "paired_difference": (successes - base) / len(rows),
        "halted_items": halted,
        "incomplete": bool(halted),
        "one_sided_bootstrap_95_lower": None if halted else samples[499],
        "non_inferior_at_minus_5pp": None if halted else samples[499] > -0.05,
        "rerank_mean_reciprocal_rank": sum(row["rerank_reciprocal_rank"] for row in rows) / len(rows),
        "elapsed_seconds": seconds,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = run(args.db, model=args.model, k=args.k)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, indent=2))


if __name__ == "__main__":
    main()
