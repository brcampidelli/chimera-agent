"""JSON keyword recall × SQLite/FTS5 recall, paired. Registered in `PREREGISTRATION.md`.

Deterministic, offline, US$ 0: the repository's own test names as facts, four query styles derived
from the target by rule, both backends behind the production `MemoryManager.search`, k = 5.

    python bench/memory_recall/run.py            # prints the tables; writes results/table.json
"""

from __future__ import annotations

import json
import random
import re
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.memory.manager import MemoryManager  # noqa: E402
from chimera.memory.sqlite_store import SqliteMemoryStore  # noqa: E402
from chimera.memory.store import MemoryStore  # noqa: E402
from chimera.memory.tokens import informative, tokens  # noqa: E402

K = 5
SEEDS = (1, 2, 3)
TARGETS_PER_SEED = 100
STYLES = ("sentence", "needle", "noisy", "partial")
COMMON_POOL = 30


def facts_from_tests() -> list[str]:
    names: set[str] = set()
    for path in sorted((REPO / "tests").glob("test_*.py")):
        for m in re.finditer(r"^def (test_[a-z0-9_]+)\(", path.read_text(encoding="utf-8"), re.M):
            names.add(m.group(1)[5:].replace("_", " "))
    return [n for n in sorted(names) if len(informative(tokens(n))) >= 4]


def make_queries(facts: list[str], rng: random.Random) -> list[dict[str, Any]]:
    """The four styles for `TARGETS_PER_SEED` targets, from the corpus's own frequencies."""
    terms_of = {f: sorted(informative(tokens(f))) for f in facts}
    df: Counter[str] = Counter()
    for terms in terms_of.values():
        df.update(set(terms))
    common = [t for t, _ in df.most_common(COMMON_POOL)]
    vocabulary = sorted(df)
    targets = rng.sample(facts, TARGETS_PER_SEED)
    out: list[dict[str, Any]] = []
    for target in targets:
        terms = terms_of[target]
        by_rarity = sorted(terms, key=lambda t: (df[t], t))
        rare2 = by_rarity[:2]
        rare3 = by_rarity[:3]
        needle = list(rare2)
        rng.shuffle(needle)
        noise = rng.sample([c for c in common if c not in terms], 3)
        wrong = rng.choice([v for v in vocabulary if v not in terms])
        partial = list(rare3)
        partial[rng.randrange(3)] = wrong
        rng.shuffle(partial)
        out.append({
            "target": target,
            "sentence": " ".join(terms),
            "needle": " ".join(needle),
            "noisy": " ".join(needle + noise),
            "partial": " ".join(partial),
        })
    return out


def build(kind: str, facts: list[str], root: Path) -> MemoryManager:
    store: Any
    if kind == "json":
        store = MemoryStore(root / "memory.json")
    else:
        store = SqliteMemoryStore(root / "memory.db")
    manager = MemoryManager(store)
    for i, fact in enumerate(facts):
        manager.add(fact, key=f"f{i}")
    return manager


def measure(manager: MemoryManager, queries: list[dict[str, Any]]) -> dict[str, Any]:
    """recall@1 / recall@5 / MRR@5 per style, the per-query hit lists, and the latencies."""
    hits: dict[str, list[dict[str, Any]]] = {s: [] for s in STYLES}
    latencies: list[float] = []
    for q in queries:
        for style in STYLES:
            began = time.perf_counter()
            results = manager.search(q[style], k=K)
            latencies.append(time.perf_counter() - began)
            got = [item.content for item in results]
            rank = got.index(q["target"]) + 1 if q["target"] in got else 0
            hits[style].append({"query": q[style], "target": q["target"], "rank": rank})
    summary: dict[str, Any] = {}
    for style in STYLES:
        ranks = [h["rank"] for h in hits[style]]
        summary[style] = {
            "recall@1": round(sum(1 for r in ranks if r == 1) / len(ranks), 3),
            f"recall@{K}": round(sum(1 for r in ranks if 1 <= r <= K) / len(ranks), 3),
            f"mrr@{K}": round(sum(1 / r for r in ranks if 1 <= r <= K) / len(ranks), 3),
        }
    latencies.sort()
    summary["latency_ms"] = {
        "median": round(1000 * statistics.median(latencies), 2),
        "p95": round(1000 * latencies[int(0.95 * (len(latencies) - 1))], 2),
        "n": len(latencies),
    }
    return {"summary": summary, "hits": hits}


def main() -> None:
    facts = facts_from_tests()
    sizes = (200, 1000, len(facts))
    print(f"facts: {len(facts)}  sizes: {sizes}  k={K}  seeds={SEEDS}")
    table: dict[str, Any] = {"facts": len(facts), "sizes": list(sizes), "k": K, "cells": []}
    for n in sizes:
        for seed in SEEDS:
            rng = random.Random(seed)
            subset = rng.sample(facts, n) if n < len(facts) else list(facts)
            queries = make_queries(subset, random.Random(seed * 1000 + n))
            arms: dict[str, dict[str, Any]] = {}
            # `ignore_cleanup_errors`: on Windows the SQLite handle keeps the file open until the
            # store is collected, and the directory is scratch either way.
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                for arm in ("json", "sqlite"):
                    manager = build(arm, subset, Path(tmp) / arm)
                    arms[arm] = measure(manager, queries)
                    conn = getattr(manager.store, "_conn", None)
                    if conn is not None:
                        conn.close()
            disagree = {}
            for style in STYLES:
                j = arms["json"]["hits"][style]
                s = arms["sqlite"]["hits"][style]
                only_json = sum(1 for a, b in zip(j, s, strict=True) if 1 <= a["rank"] <= K and not 1 <= b["rank"] <= K)
                only_sqlite = sum(1 for a, b in zip(j, s, strict=True) if 1 <= b["rank"] <= K and not 1 <= a["rank"] <= K)
                disagree[style] = {"json_only": only_json, "sqlite_only": only_sqlite}
            cell = {
                "n": n, "seed": seed,
                "json": arms["json"]["summary"], "sqlite": arms["sqlite"]["summary"],
                "disagree@k": disagree,
            }
            table["cells"].append(cell)
            print(f"\nN={n} seed={seed}")
            for style in STYLES:
                a, b = cell["json"][style], cell["sqlite"][style]
                d = disagree[style]
                print(
                    f"  {style:9s} json r@1={a['recall@1']:.3f} r@5={a[f'recall@{K}']:.3f} "
                    f"mrr={a[f'mrr@{K}']:.3f} | sqlite r@1={b['recall@1']:.3f} "
                    f"r@5={b[f'recall@{K}']:.3f} mrr={b[f'mrr@{K}']:.3f} | "
                    f"only-json={d['json_only']} only-sqlite={d['sqlite_only']}"
                )
            lj, ls = cell["json"]["latency_ms"], cell["sqlite"]["latency_ms"]
            print(
                f"  latency   json median={lj['median']} ms p95={lj['p95']} | "
                f"sqlite median={ls['median']} ms p95={ls['p95']}"
            )
    out = REPO / "bench" / "memory_recall" / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "table.json").write_text(json.dumps(table, indent=2), encoding="utf-8")
    print(f"\nwrote {out / 'table.json'}")


if __name__ == "__main__":
    main()
