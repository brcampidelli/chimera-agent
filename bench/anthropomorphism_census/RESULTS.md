# Study 31, item A31-06 — results

Offline descriptive census; **US$0**, no model call, no prompt change. Unit: distinct exact non-empty answer string.

Corpus: **3,206 distinct answers** from **69 tracked result files**. The full file manifest is in `results/census.json`.

## Rates

| Category | Hits / answers | Rate | Wilson 95% interval | Hand-read precision |
|---|---:|---:|---:|---:|
| Validation openers | 4/3206 | 0.12% | [0.05%, 0.32%] | 4/4 = 100.0% |
| Affective first person | 5/3206 | 0.16% | [0.07%, 0.36%] | 0/5 = 0.0% |
| Relationship claims | 0/3206 | 0.00% | [0.00%, 0.12%] | not applicable (0 hits) |
| Completion claims | 202/3206 | 6.30% | [5.51%, 7.20%] | 19/20 = 95.0% |

Hand-read sample: first 20 distinct hits in deterministic answer order (or all if fewer), read in answer context only. Sample-level decisions and hit texts are recorded in the JSON artifact.

## Completion claims × false-success labels

**Unavailable.** `bench/false_success` and `bench/claim_vs_diff` retain aggregate results/corpus metadata but no committed answer strings joinable to their per-run false-success labels. No external/local corpus was read and no inferred join was made.

## Limits

This counts benchmark transcripts committed in this repository, not the owner's real chats. The corpus is dominated by one model family and benchmark-specific answer styles; results reflect which benches retained answers. Lexical matches over-read context, and the small hit sample estimates false-positive precision only—not recall or overall classification accuracy. Exact-string deduplication does not collapse paraphrases. The Wilson intervals describe binomial uncertainty for this descriptive hit rate, not representativeness outside this fixed corpus.

No causal or population-level claim follows from these rates.
