# Pre-registration — a Noul per chunk as a reranker over the hybrid top-30

*Written 2026-09-22, before `run.py` produced a number. Study 21 §4 B7 (`bench/PLAN-study21-jev-ecosystem.md`): "hybrid top-30, one Noul per chunk, re-order, recall@10 paired against hybrid RRF (0.5050). Bar: +5 pp, the one the RAG registration used. Prediction: no gain alone."*

## 1. The question

`chimera find`'s semantic half was adopted on 2026-09-04 (`bench/rag/RESULTS.md`): hybrid RRF over FTS5 and `text-embedding-3-small` reads recall@10 = 0.5050 on 400 probes, +6.25 pp over keyword alone. Study 21 catalogued a reranker built on typed decisions (jev-search / jev-search-rerank-eval) and an independent measurement where that reranker lost to `bge-m3`. The question here is the cheapest form of the idea on our own bench:

> **Does re-ordering the hybrid top-30 by a local yes/no decision per chunk — "does this code implement what the query describes?" — lift recall@10 over hybrid RRF by the bar the RAG registration set?**

## 2. Apparatus — the RAG bench, unchanged

`chimera.eval.rag_bench`: the corpus is `chimera/` at the commit this runs on; probes are the first docstring line of each documented symbol (the docstring is stripped from the index); the target is the symbol's own chunk; recall@10 is a hit when the target's ident is in the top ten. Embedder `openrouter/openai/text-embedding-3-small` (≈ US$ 0.02 for the corpus and the 400 queries, the only spend). Same probe builder, same store, same RRF, same `k`.

## 3. Arms

| arm | top-10 is |
|---|---|
| `hybrid` (baseline) | RRF of keyword@10 and vector@10, limit 10 — the published arm, re-measured on today's corpus |
| `hybrid30` | RRF of keyword@30 and vector@30, limit 30, then the first 10 — the candidate set the reranker sees; reported so the reranker's gain is read against what it was given |
| **`noul`** | `hybrid30`, each of the 30 chunks asked the Noul, re-ordered by `P(yes)` descending, ties by RRF rank |
| `random` (control) | `hybrid30` re-ordered uniformly at random (seed fixed) — the floor a reranker that reads nothing would produce |

The Noul: `qwen3:4b` through Ollama, the bench's own local instrument (`bench/jev_decisions/run.py::local`, decision-first, JSON-enum `{"verdict": "YES"|"NO"}`, label token from the end, temperature 0). State: the query (the docstring line) and the chunk's path, symbol and text (capped at 1,500 characters). Question: *"Is this code the definition that the description below describes? Answer YES only if this chunk defines it, not if it merely calls or mentions it."*

**Only probes whose target is inside the hybrid top-30 are sent to the model.** A probe whose target is not among the 30 cannot become a hit under any re-ordering of those 30, and the outcome is identical either way — this is bookkeeping, not a shortcut on the measurement, and the count of skipped probes is printed.

## 4. Primary measurement and decision rule

Paired over the same probes: recall@10 of `noul` minus recall@10 of `hybrid`, McNemar exact on the discordant pairs, 95% CI on the paired delta — the apparatus of `chimera/eval/paired.py`, as `bench/rag` used it.

**ADOPT** (a reranking step in `chimera find --semantic`) only if **delta ≥ +5.0 pp, p < 0.01, and `noul` ≥ `hybrid30`** (a reranker that loses to the unreranked candidate set is not a reranker). Otherwise: publish the null.

## 5. Predictions

- **P1.** `noul` − `hybrid` is **below +5 pp** — the plan's "no gain alone". A 4B model deciding "is this the definition" from a docstring line and a code chunk is a weaker signal than the fusion of two rankers that already agree on the target.
- **P2.** `noul` ≥ `random` by at least +5 pp — the model reads *something*; if it does not, the Noul is noise and P1 is not about the model.
- **P3.** `hybrid30` (first 10 of the 30) equals `hybrid` on ≥ 95% of probes — widening the candidate set changes the top ten little; the reranker's headroom is the probes where the target sits at ranks 11–30, and that count is printed as the ceiling.

## 6. Controls

- **paired:** `hybrid` must land within ±0.03 of the published 0.5050 on today's corpus (the corpus has grown since 2026-09-04; `bench/rag` §"the corpus moved" says why the tolerance is not zero — the chunk count is printed beside it).
- **positive:** an arm that re-orders by the oracle (target first) must read recall@10 = the ceiling exactly (every probe whose target is in the 30).
- **negative:** `random` ≤ `hybrid30` + 0.02.
- **Three Noul readings printed with their raw content** before the numbers are trusted (§2e).

## 7. What this cannot show (§2q)

- `bge-m3` or any cross-encoder reranker — not run; the plan's comparison is to the *idea* measured elsewhere, not a head-to-head here.
- Anything beyond first-docstring-line probes on one Python corpus (`bench/rag` §"one corpus, one language").
- Latency as a product cost: 30 local calls per query at ~0.4 s is ~12 s a search, which alone would keep this off the default path even if it passed; the number is recorded, not modelled.

## 8. Cost

≈ US$ 0.02 (embeddings). The Noul is local, US$ 0; wall-clock ≈ 30 × (probes with the target in the 30) × 0.4 s.
