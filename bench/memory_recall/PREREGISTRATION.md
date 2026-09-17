# Pre-registration — should the memory backend default to SQLite/FTS5?

**Registered 2026-09-17 against `5a6bed3` (v0.58.0 + #499–#503), before the script was run.**
Deterministic and offline: no model, no key, US$ 0. Item 5 of the list audited on 2026-09-16 said
*"measure JSON × SQLite recall before flipping the default"*; this is that measurement.

## Where the question comes from

`CHIMERA_MEMORY_BACKEND` defaults to `json` (`chimera/config.py:309`). The JSON store recalls by
keyword overlap: `MemoryManager.search` (`chimera/memory/manager.py:289`) tokenizes **every stored
fact on every query**, weights the matched terms by inverse document frequency over the whole store
(`idf_weights`), and sorts by that sum, ties by id. The SQLite store (`chimera/memory/sqlite_store.py`)
keeps an FTS5 index and lets `MATCH … ORDER BY rank` (bm25) decide, through the same tokenizer and
the same function-word list. Both are reached through the same `MemoryManager.search`, which is what
every turn calls.

Nothing has compared them. `chimera/eval/memory_bench.py` measures the lexical-vs-paraphrase gap with
a one-word needle probe, on which any lexical search scores 1.0 — no signal between the two arms
(§2h: a ruler both arms saturate measures nothing). And a default flip has a cost the bench cannot
see: there is no JSON→SQLite import in the product, so flipping the default would leave every
existing `memory.json` unread — a memory that silently vanishes, the family of defect this project
keeps a file about.

## What is measured

**Facts.** The 4,034 test names of this repository with at least four informative tokens, as
sentences (`test_a_backend_that_never_streams_still_answers` → *"a backend that never streams still
answers"*): short, English, one project's vocabulary, heavily shared words (`turn`, `tool`, `run`,
`session`), which is what a working memory store looks like. No text is generated for this bench.

**Queries.** For 100 target facts per seed (seeds 1, 2, 3), four styles derived from the target,
identical for both arms:

| style | the query |
|---|---|
| `sentence` | every informative token of the fact — the control: both arms must find it |
| `needle` | the fact's two rarest tokens by corpus document frequency, order shuffled |
| `noisy` | the two rarest tokens plus three of the corpus's 30 most common informative tokens |
| `partial` | the three rarest tokens with one replaced by a token the fact does not contain — a mis-remembered word |

**Store sizes.** N = 200, 1,000 and 4,034 (all), the targets always among the N.

**Arms.** `MemoryManager(MemoryStore(json))` and `MemoryManager(SqliteMemoryStore(db))`, semantic
off, `k = 5`, every fact `add`ed through the manager the way production writes them.

**Outcomes.** Per style × N × seed × arm: recall@1, recall@5, MRR@5. Paired per query: on how many
queries the arms disagree at @5, and in which direction. Latency: median and p95 of one `search`,
per N × arm, over the 400 queries of one seed, on this machine (i7 laptop, Windows 11, Python 3.11
in the repo venv — stated because a latency number is of a machine).

## Registered predictions

1. **`sentence` ≥ 0.98 recall@5 on both arms at every N.** If either is below, the apparatus is
   wrong, not the backend (§2aa: a control that does not reproduce a known number).
2. **Recall is indistinguishable**: on `needle`, `noisy` and `partial`, |Δ recall@5| ≤ 0.03 between
   the arms at every N, because for one-sentence facts where a term occurs once, bm25 reduces to an
   IDF sum with mild length normalisation — the same quantity the JSON path sums. Disagreements at
   @5 will be ties broken differently, under 5% of queries.
3. **Latency separates them**: the JSON arm re-tokenizes the store per query, so its median grows
   linearly with N and exceeds 20 ms at N = 4,034; the SQLite arm stays under 5 ms at every N.
   At N = 200 both are under 5 ms and the difference is not worth a default.

## Decision rule

The default flips to `sqlite` **only if all three hold**: (a) recall@5 of the SQLite arm ≥ the JSON
arm − 0.02 on every style at every N, all three seeds; (b) SQLite median latency ≤ JSON median at
N = 1,000; (c) the flip ships **with** a one-time import of an existing `memory.json` into a new
`memory.db`, so no store goes silent, and with a test that an install with a JSON store and no db
keeps every fact after the flip.

If (a) fails on any cell, nothing flips and the cell is published. If (a) and (b) hold, (c) is the
work and the flip happens in the same PR as the import, not before it.

## What this cannot show

Paraphrase recall (no shared token) is zero on both arms by construction and is the semantic
layer's job, measured elsewhere. The queries are derived from the target by rule, so they are
easier than a person's and identical in difficulty for both arms — this measures the difference
between the arms, not recall in the wild. One machine's latency. And a store of one-sentence facts:
a store of long facts would make bm25's length normalisation matter, which this corpus cannot show.
