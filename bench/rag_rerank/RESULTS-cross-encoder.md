# Results — BGE cross-encoder over the hybrid top-30 (S30-62)

*Run 2026-10-07. Pre-registration: `PREREGISTRATION-cross-encoder.md` (2026-10-06) with its §8
execution note, committed before any score (`8b315575`). Model `BAAI/bge-reranker-v2-m3` at Hub
revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, fp32, default `CrossEncoder` settings,
sentence-transformers 6.1.0 / transformers 5.19.0 / torch 2.9.1+cu128 on an RTX 5070 Laptop GPU (8 GB).
Corpus `chimera/` at origin/main `4ee2bd1a`, **6,300 chunks**, 400 probes; embedder
`openrouter/openai/text-embedding-3-small` (≈ US$ 0.03 a pass; two passes, see §6). Rows:
`results/2026-10-06-cross-encoder.jsonl` (file name as registered); summary:
`results/2026-10-06-cross-encoder.summary.json`.*

## The verdict in one line

**NULL — no reranking step.** The cross-encoder does not move recall@10: **0.3300 → 0.3250**, paired
delta **−0.5 pp**, 95% CI [−4.0, +3.0], exact McNemar p = 0.89 (24 wins, 26 losses), and it ends
**below** the candidate set it was handed (hybrid30 0.3450). All three registered conditions fail:
delta < +5 pp, p ≥ 0.01, cross-encoder < hybrid30.

## 1. Recall@10

| arm | recall@10 | note |
|---|---:|---|
| `hybrid` (RRF of keyword@10 + vector@10) | 0.3300 | the baseline, today's corpus |
| `hybrid30` (first 10 of the RRF over 30) | 0.3450 | the candidate set the reranker sees |
| **`cross_encoder`** (the 30 re-ordered by BGE logit) | **0.3250** | |
| `random` (the 30 shuffled, original seed) | 0.1625 | negative control |
| `oracle` (target first) | 0.4675 | = 187/400, the ceiling |

Target inside the 30 on 187 probes; at ranks 11–30 on **49** — all the headroom a perfect reranker had.

## 2. Paired

| comparison | Δ | 95% CI | discordant (treatment + / baseline +) | p |
|---|---:|---|---|---:|
| `cross_encoder` vs `hybrid` (**primary**) | **−0.50 pp** | [−4.0, +3.0] | 24 / 26 | 0.89 |
| `cross_encoder` vs `hybrid30` (diagnostic) | −2.00 pp | [−5.2, +1.2] | 17 / 25 | — |
| `cross_encoder` vs `random` (control) | +16.25 pp | [+11.3, +21.0] | 86 / 21 | — |

Where they come from, on the 187 scored probes: the 25 losses against hybrid30 had the target at a
median RRF rank **5** (none at rank 1) and the cross-encoder pushed it to a median **14**; the 17 wins
had it at a median **17** and lifted it to **4**. Overall the target's median rank is 5 under both
orderings, and it is first on 31 probes under the cross-encoder against 32 under RRF. It is a
re-shuffle of about equal size in both directions, not a reranker that reads "is this the
definition?" better than the fusion does.

Against the Noul arm (`RESULTS.md`): the Noul lost 7.8 pp with p = 0.002; the cross-encoder loses
nothing measurable and gains nothing either. A retrieval-trained reranker is a better instrument than
a yes/no from a small general model — and still not one that earns a step.

## 3. Controls

- **paired:** `hybrid` reads **0.3300 against the published 0.5050** — far outside ±0.03. The corpus
  is **6,300 chunks** against 3,459 on 2026-09-04 and 4,177 on 2026-09-22 (the Noul run, hybrid
  0.4175), and `build_probes` takes the first 400 of a longer list, so the probe set moved too. This
  is the slope `bench/rag` §"the corpus moved" already recorded, one step further. The within-run
  comparison is paired on the same probes in the same index, which is what the rule needs; the
  absolute figure is not comparable to either published one. §4 below is the diagnostic that holds
  the corpus fixed.
- **positive:** `oracle` = 187/400 = the ceiling exactly.
- **negative:** `random` 0.1625 ≤ `hybrid30` + 0.02.
- `hybrid30` top-10 equals `hybrid` on 93.0% of probes (the Noul run: 94.0%).

## 4. Diagnostic — the same arm on the Noul's corpus (`9550a90`)

*Declared in the §8 execution note before any score: it cannot replace the verdict above.*

Corpus `git archive 9550a90 chimera/` (4,177 chunks), same command plus `--corpus`. Rows:
`results/2026-10-07-cross-encoder-snapshot-9550a90.jsonl`; summary beside it. 35.5 min of scoring
for 218 probes.

**Control — it reproduces the Noul run.** The 400 probes match the Noul rows query for query and
target for target; `hybrid` is **0.4175, exactly the published Noul-run figure**, and the per-probe
`hybrid` and `hybrid30` hits agree on **400/400** rows. Target-in-30 agrees on 399/400 (218 against
219: one probe whose target sat at the edge of the 30 under a re-embedding — the embedder is a hosted
call, not bit-reproducible), so the oracle reads 0.5450 against 0.5475.

| comparison (same 400 probes) | Δ | 95% CI | discordant (+ / −) | p |
|---|---:|---|---|---:|
| `cross_encoder` 0.4250 vs `hybrid` 0.4175 | +0.75 pp | [−2.7, +4.2] | 25 / 22 | 0.77 |
| `cross_encoder` vs `hybrid30` 0.4225 | +0.25 pp | [−3.3, +3.8] | 26 / 25 | — |
| `cross_encoder` vs `random` 0.1725 | +25.25 pp | [+20.0, +30.3] | 118 / 17 | — |
| `cross_encoder` vs the **Noul** 0.3400 (`RESULTS.md`) | **+8.5 pp** | [+3.9, +13.1] | 62 / 28 | 0.0004 |

On the Noul's own corpus the cross-encoder is level with the fusion and clearly better than the Noul
— the two rerankers differ, and the difference is the Noul's damage, not the cross-encoder's gain.
Would this have been an ADOPT under the rule? No: +0.75 pp is far from +5 pp, p = 0.77. **Both
corpora say the same thing**, which is what lets the null be read as the model's rather than as an
accident of the larger, moved corpus. (On today's corpus P2 failed narrowly; here it holds narrowly —
both readings are inside ±2 pp of hybrid30, i.e. a tie.)

## 5. Against the registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | cross-encoder − hybrid more plausibly ≥ +5 pp than for the Noul; only the rule decides | **refuted** in direction: −0.5 pp, CI upper bound +3.0, so +5 pp is outside the interval |
| P2 | cross-encoder ≥ hybrid30 | **refuted** — 0.3250 < 0.3450 (−2.0 pp, CI [−5.2, +1.2]) |
| P3 | the top-30 ceiling bounds recall; headroom is the opportunity | **confirmed** — oracle = 187/400; headroom 49 probes, of which the reranker converted 17 and gave back 25 |

## 6. What this decides, and what it cannot show

- **No reranking step in `chimera find --semantic`**, for this model as for the Noul. The fusion stays.
- **Latency would rule it out on this machine regardless:** 97.9 min of scoring for 187 probes —
  median 3.9 s a probe, p95 139 s — because the GPU was shared with an Ollama `qwen3:4b` that kept
  4 GB resident and the fp32 model spilled into shared memory. Alone it would be faster; even at the
  median it is seconds a search, against milliseconds for the fusion.
- Cannot show: other rerankers (bge-reranker-v2-gemma, a code-trained one), a different candidate
  text (full chunk, symbol-only), a different probe kind — probes are first docstring lines on one
  Python corpus (`bench/rag` §"one kind of question"). The external biomedical result
  (arXiv 2610.01324, MedCPT +14 pp Hit@10) is a different domain and a domain-trained model; this
  null does not contradict it and is not evidence about it.
- **Cost line, honestly:** a first launch of the registered command was stopped by me after 128
  probes because it would have outlived its shell timeout (the GPU contention above); it was
  relaunched from scratch with the same command and the partial rows were overwritten, their outcomes never read
  for the verdict. That is one extra embedding pass (≈ US$ 0.03).
