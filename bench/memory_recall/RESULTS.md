# Results — should the memory backend default to SQLite/FTS5?

**Run 2026-09-17 against `5a6bed3`, as registered in `PREREGISTRATION.md`.** 4,034 facts (this
repository's test names), 100 targets × 4 styles × 3 seeds × 3 store sizes = 3,600 paired queries
per arm, `k = 5`, both arms behind the production `MemoryManager.search`, semantic off. US$ 0.
Latency on an i7 laptop, Windows 11, Python 3.11 (`.venv`). Raw table: `results/table.json`.

## Recall — the two stores rank the same quantity

recall@5, per cell (json / sqlite):

| N | seed | sentence | needle | noisy | partial | disagree @5 (json-only / sqlite-only) |
|---:|---:|---|---|---|---|---|
| 200 | 1 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 on every style |
| 200 | 2 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 |
| 200 | 3 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 |
| 1000 | 1 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 |
| 1000 | 2 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 0.990 | partial 1 / 0 |
| 1000 | 3 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 |
| 4034 | 1 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 |
| 4034 | 2 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0 / 0 |
| 4034 | 3 | 1.000 / 1.000 | 0.990 / 1.000 | 0.990 / 1.000 | 1.000 / 0.990 | needle 0 / 1 · noisy 0 / 1 · partial 1 / 0 |

recall@1 moves more, and in both directions — at N = 4,034 it is 0.89–0.94 (json) against
0.85–0.97 (sqlite) on the three derived styles, seed by seed on either side. That is the ties:
two facts sharing the query's rare terms, ordered by id in one store and by bm25's length
normalisation in the other. MRR@5 differs by at most 0.023 on any cell.

**Prediction 1 (control) held**: `sentence` is 1.000 on both arms at every N.
**Prediction 2 held**: |Δ recall@5| ≤ 0.01 on every cell (registered ≤ 0.03); the arms disagree at
@5 on 0–1 of 100 queries per cell (registered < 5%).

## Latency — the JSON path pays for the whole store on every query

median / p95 of one `search`, ms, over 400 queries:

| N | json | sqlite |
|---:|---:|---:|
| 200 | 3.2 / 4.1 | 0.13 / 0.18 |
| 1,000 | 16.4 / 19.1 | 0.16 / 0.25 |
| 4,034 | 70 / 81 | 0.18 / 0.40 |

**Prediction 3 held**: the JSON median is linear in N (3.2 → 16.4 → 70 ms, ×5 for ×5 facts), above
20 ms at N = 4,034; the SQLite median is under 0.5 ms at every N (registered < 5 ms). The JSON cost
is `MemoryManager.search` re-tokenizing every stored fact and recomputing IDF per query — by
design, and the right design for a store of tens of facts.

## Decision

The registered rule: flip only if (a) sqlite recall@5 ≥ json − 0.02 on every cell — **held, worst
cell −0.01**; (b) sqlite median latency ≤ json at N = 1,000 — **held, 0.16 vs 16.4 ms**; (c) the flip
ships with a one-time import of an existing `memory.json` — **done in the same PR**
(`chimera/memory/backend.py`), with the tests the rule asked for.

So `CHIMERA_MEMORY_BACKEND` defaults to `sqlite`, resolved to `json` on a Python without FTS5
(the SQLite store's `LIKE` fallback has no ranking, and a default must not trade ranked recall for
unranked). The import preserves every field; both files existing means the database wins; an
unreadable JSON store is not shadowed.

## Found on the way, not registered

- **The SQLite store dropped `created_at`.** `MemoryItem` has carried the age since
  `test_a_memory_had_no_age.py`, the JSON store round-trips it, and `SqliteMemoryStore` had no
  column for it — a fact written through SQLite came back ageless. Fixed in the same PR with a
  rebuild migration (old rows read `None`, never a plausible age), and the round-trip is now pinned
  on the SQLite side too.
- **`MemoryStore.add` rewrites the whole file per add** (the bench's 4,034 adds took minutes on the
  JSON arm, seconds on SQLite). Not measured here — the bench times `search` — and not a reason for
  the flip, since production adds one fact at a time. Stated so nobody reads the runtime of
  `run.py` as a search cost.

## What this did not show

The queries are derived from the target by rule and are the same for both arms; they measure the
difference between the stores, not recall in the wild. Paraphrase recall is zero on both by
construction (the semantic layer's job). One machine's latency. One-sentence facts — a store of
long facts is where bm25's length normalisation would separate the two, and this corpus cannot
show it.
