# Results — a Noul per chunk as a reranker over the hybrid top-30

*Run 2026-09-22. Corpus `chimera/` at `9550a90`, **4,177 chunks**, 400 probes; embedder `openrouter/openai/text-embedding-3-small` (≈ US$ 0.02, plus one wasted pass — see §6); reranker `qwen3:4b` Q4_K_M through Ollama, 219 probes × 30 chunks = 6,570 local calls, 52 minutes. Pre-registration: `PREREGISTRATION.md`, written before the run. Rows: `results/2026-09-22-rerank-local.jsonl`; summary: `results.json`.*

## The verdict in one line

**Null — no reranking step. The Noul makes retrieval worse:** recall@10 **0.4175 → 0.3400**, paired delta **−7.8 pp**, 95% CI [−11.9, −3.0], McNemar p = 0.002, on 400 probes. It beats a random re-ordering of the same 30 by +14.5 pp — the model reads *something* — and it still loses 59 probes the fusion had in its top ten while winning 26. A reranker that loses to the candidate set it was handed is not a reranker.

## 1. Recall@10

| arm | recall@10 | note |
|---|---:|---|
| `hybrid` (RRF of keyword@10 + vector@10) | 0.4175 | the baseline, today's corpus |
| `hybrid30` (first 10 of the RRF over 30) | 0.4225 | the candidate set the reranker sees |
| **`noul`** (the 30 re-ordered by P(yes)) | **0.3400** | |
| `random` (the 30 re-ordered uniformly) | 0.1950 | negative control |
| `oracle` (target first) | 0.5475 | = 219/400, the ceiling — every probe whose target is in the 30 |

Target inside the 30 on 219 probes; at ranks 11–30 on **50** — that is all the headroom a perfect reranker had.

## 2. Paired

| comparison | Δ | 95% CI | discordant (treatment + / baseline +) | p |
|---|---:|---|---|---:|
| `noul` vs `hybrid` | **−7.75 pp** | [−11.9, −3.0] | 32 / 63 | 0.002 |
| `noul` vs `hybrid30` | −8.25 pp | [−12.0, −3.8] | 26 / 59 | — |
| `noul` vs `random` | +14.5 pp | [+9.2, +19.0] | 93 / 35 | — |

Where the 59 losses come from: the target sat at a median rank **4** of the 30 (46 targets were at rank 1) and the Noul moved it to a median rank **16**, giving it P(yes) 0.12 while some other chunk in the same 30 got 0.79. The 26 wins are the mirror: targets at a median rank 20 that the model lifted. Over all 219 probes sent, the target's P(yes) has a median of 0.29 and the best *other* chunk 0.75: **the model prefers a different chunk to the true definition on 192 of 219 probes.** A 4B model asked "is this the definition?" from a one-line docstring and a code chunk answers a different question than the one the fusion answers — probably "does this chunk look like it belongs to that description", which a caller or a sibling satisfies as well as the definition.

## 3. Controls

- **paired:** `hybrid` reads **0.4175 against the published 0.5050** — outside the ±0.03 tolerance. The corpus is 4,177 chunks against 3,459 on 2026-09-04 (+21%), and `build_probes` takes the *first* 400 documented symbols of a larger list, so the probe set moved too. `bench/rag` §"the corpus moved" already recorded recall@10 falling as the haystack grows (0.4925 at 2,691 → 0.4425 at 3,459 for keyword); this is the same slope one step further, **and** a different probe sample. The within-run comparisons are paired on the same probes in the same index, which is what they need; the absolute figure is reported with the chunk count beside it and is not comparable to the published one.
- **positive:** `oracle` = 219/400 = the ceiling exactly.
- **negative:** `random` 0.1950 ≤ `hybrid30` + 0.02.
- Three raw readings printed before the numbers were trusted: `{"verdict": "NO"}` with P(yes) 0.22–0.33 on non-targets — the label was read from the JSON, not from prose.

## 4. Against the registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | `noul` − `hybrid` below +5 pp | **confirmed** — and it is −7.8, not merely below +5 |
| P2 | `noul` beats `random` by ≥ 5 pp | **confirmed** — +14.5 |
| P3 | `hybrid30` top-10 equals `hybrid` on ≥ 95% of probes | **refuted, narrowly** — 94.0% (24 probes differ; widening the candidate lists changes the RRF top ten more than I predicted) |

## 5. What this decides

- **No reranking step in `chimera find --semantic`.** The fusion stays as adopted on 2026-09-04.
- **The idea is not "a reranker"; it is "this reranker".** A cross-encoder trained for retrieval (the `bge` family study 21 cited) is a different instrument and was not run; this bench says nothing about it beyond the fact that a yes/no decision from a small general model is not it.
- **Latency would have ruled it out anyway:** 30 calls at ~0.5 s is 14 s a search on a laptop GPU.

## 6. What this cannot show, and one apparatus note (§2q)

- `bge-m3` / a cross-encoder — not run.
- Probes are first docstring lines on one Python corpus (`bench/rag` §"one kind of question").
- Whether the same Noul with the *full* chunk (the state cut at 1,500 characters) or a different question ("does this chunk contain the symbol named…") would do better — a different instrument, its own registration.
- The first run of this bench died after the embedding pass because the bench directory was moved while it ran (a `mv` of mine, checking an unrelated test). US$ 0.02 wasted; recorded so the cost line is honest. The rule that follows is in memory: the tree of a running bench is not touched.
