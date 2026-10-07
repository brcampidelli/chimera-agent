# Pre-registration — BGE cross-encoder over the hybrid top-30

*Written 2026-10-06, before the cross-encoder arm is run. Study 30, S30-62. This addendum fills the cross-encoder gap named in `RESULTS.md` §6; it does not revise or reinterpret the Noul result.*

## 1. Question and scope

> **Does a locally run, retrieval-trained BGE cross-encoder that re-orders the hybrid top-30 raise recall@10 over hybrid RRF by the original registration's adoption bar?**

This is a single-corpus, single-probe-cohort measurement. It does not estimate generalization or establish latency suitability. No reranker option ships unless the registered paired rule below is met.

## 2. Instrument and unchanged apparatus

Use the local Sentence Transformers `CrossEncoder` for model **`BAAI/bge-reranker-v2-m3`**. Score each (query, candidate-text) pair with the model's relevance logit and sort descending; ties retain the candidate's original RRF rank. Candidate text is exactly the state already used in `run.py`: path, symbol, kind, and source text capped at 1,500 characters. Do not add a prompt or alter the query/chunk text for this arm.

Use the original RAG apparatus unchanged: the same `chimera/` corpus walk and docstring stripping, first 400 probes from `build_probes`, embedding model/settings, store, keyword/vector retrieval and RRF. For each probe construct the same `hybrid` top-10 and `hybrid30` RRF top-30; the cross-encoder sees only those exact 30 candidates. Evaluate on all 400 probes. A target outside the top-30 cannot enter the cross-encoder top ten and is recorded as a miss without being scored. No candidates, probes, corpus, or baseline rows may be dropped after observing scores.

The model is optional and imported only when this arm is requested. Install/use a local model cache before the measurement; this preregistration does not authorize a model download during implementation. Measurement command (after the optional local dependency is installed and the model is available locally):

```cmd
uv run --extra dev --extra desktop python bench/rag_rerank/run.py --run-cross-encoder --max-probes 400 --out bench/rag_rerank/results/2026-10-06-cross-encoder.jsonl
uv run --extra dev --extra desktop python bench/rag_rerank/run.py --report-cross-encoder --out bench/rag_rerank/results/2026-10-06-cross-encoder.jsonl
```

The report must record model id, probe/corpus counts, candidate ceiling/headroom, arm recalls, paired counts, paired delta and 95% CI, exact McNemar p-value, total scoring time, and the decision. Preserve the raw per-probe rows.

## 3. Arms

| arm | top-10 is |
|---|---|
| `hybrid` | original RRF of keyword@10 and vector@10, limit 10 |
| `hybrid30` | first ten of the RRF over keyword@30 and vector@30, limit 30 |
| `cross_encoder` | the same hybrid30 candidates re-ordered by BGE relevance logit; ties by RRF rank |
| `random` | same hybrid30 candidates uniformly shuffled with the original fixed seed, as the existing negative control |
| `oracle` | target first when present in hybrid30; otherwise the original candidate order; ceiling/control only |

## 4. Primary measurement and decision rule

Paired on exactly the same 400 probes: recall@10(`cross_encoder`) minus recall@10(`hybrid`), using the existing `chimera.eval.paired` comparison and 95% paired-delta confidence interval; report exact two-sided McNemar p on discordant pairs, as in the original preregistration.

**ADOPT** only if all three original thresholds hold: delta **≥ +5.0 percentage points**, exact McNemar **p < 0.01**, and cross-encoder recall@10 **≥ hybrid30 recall@10**. Otherwise publish the result as **NULL — no reranking step**. This rule is unchanged from `PREREGISTRATION.md` §4; no post-hoc alternate metric or subgroup can overturn it.

## 5. Controls and reporting

- Report hybrid against the published 0.5050 with the original ±0.03 warning, alongside today's chunk count; do not filter or replace probes if it misses tolerance.
- The oracle must equal the number of probes whose target is in the top-30 divided by 400.
- Report random against hybrid30 (the original negative-control comparison); do not use this to replace the primary adoption rule.
- Report cross-encoder versus hybrid30 paired as a diagnostic, in addition to the registered primary paired comparison.
- Report the fraction for which hybrid30 top ten equals hybrid top ten, target-in-top-30 count, and ranks 11–30 headroom.
- The harness fake-scorer test validates wiring and paired row identity only. It is not measurement evidence and must not be included in result data.

## 6. Predictions

- **P1:** cross-encoder − hybrid is at least +5 pp more plausible than it was for the Noul, but adoption remains uncertain; only the fixed paired rule decides.
- **P2:** cross-encoder recall@10 is at least hybrid30, as a competent reranker should not lose against its own candidate set.
- **P3:** the target-in-top-30 ceiling bounds any possible recall; the reported headroom is the actual number of opportunities to improve beyond hybrid30.

These are predictions, not permission to alter thresholds or report only favorable readings.

## 7. Cost and limitations

US$ 0 for model scoring: run locally and do not download the model as part of this task. Record hardware, model-cache provenance, and elapsed scoring time with the eventual result. This one 400-probe cohort on one evolving Python corpus is weak evidence for general performance; no confidence interval cures that scope limitation. The external biomedical Hit@10 result (arXiv 2610.01324) motivates the arm but is not a prior measurement of this corpus or model.
