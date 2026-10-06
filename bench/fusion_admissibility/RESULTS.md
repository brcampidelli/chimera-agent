# Results — S30-52: fusion admissibility and answer-level agreement

**US$0; zero model calls.** Replay run 2026-10-06 against the already committed member rows; protocol in `PREREGISTRATION.md` was committed first (`6ca8d13a`). Reproduce with `python bench/fusion_admissibility/run.py`; machine-readable output is `readout.json`.

## Sources and shape

- `bench/judge_blind_hard/results/collect-all.jsonl`: 50 AIME rows, three per-member outputs and correctness labels; the current selective probe is the first two of a three-member panel.
- `bench/panel_correlation/results.json`: previously published hard-corpus ICC(1) = **+0.5266** (published as +0.527).
- `bench/fusion_aggregate/results/panel.jsonl`: absent in this worktree (ignored/unavailable). Arithmetic-panel member-level replay is therefore **not available**; no rows were reconstructed.

The hard panel’s pairwise correctness kappa / binary-error Pearson correlation, all n=50:

| Members | Cohen κ on correctness | Error correlation |
|---|---:|---:|
| GLM-5.3-Flash / GPT-OSS-20B | +0.348 | +0.356 |
| GLM-5.3-Flash / Kimi-K2 | +0.522 | +0.535 |
| GPT-OSS-20B / Kimi-K2 | +0.714 | +0.714 |
| **Mean pairwise error correlation** | — | **+0.535** |

This verdict aligns with the published ICC and tail counts: this AIME panel is **not admissible as three independent corroborating votes**. The pairwise numbers characterize this panel/corpus only; they are not a universal discount. The ceiling-limited arithmetic panel cannot change this finding.

## Early-stop agreement replay

Probe size=2, 50 hard-corpus items. Existing threshold is the configured default 0.8, applied to whitespace-normalised lowercase strings via `SequenceMatcher` (same computation as `_agree`). Agreement precision here means **both probe answers independently equal the reference**, not simply a correct first representative.

| Rule | Fires | Firing rate | Precision | Estimated member calls saved (best-of ≥3) | Total provider calls saved |
|---|---:|---:|---:|---:|---:|
| Existing phrasing similarity | 0/50 | 0.0% | N/A (never fires) | 0 | 0 |
| Normalised final answer equality | 12/50 | 24.0% | 100% (12/12) | 12 | 24 |

There were 15 probe pairs with a missing/unextractable `ANSWER:` line (excluded as agreement). Answer-level equality has higher firing rate and no observed precision loss, so it meets the registered eligibility criterion. **It remains OFF**: the replay does not enable a new production stop condition. Call savings are a deterministic estimate, not a live runtime result.

## Exact locking

Shipped ON: `_present` now omits exact duplicates and shorter strings that are exact prefixes of a longer panel answer; the longest representative is retained. A fake-backend regression compares the old uncollapsed presentation against the new one and asserts the judge/synth outputs remain byte-identical while the prompt retains the longest answer. This is a mechanical output-preserving optimization, not evidence of quality improvement.

## Readout shape and limits

`readout.json` has version 1, source paths, panel/probe sizes, cost/model-call counts, pair-level kappas/correlations, published ICC, both firing/precision/call-saving metrics, and an explicit absent-source status for the arithmetic data. No network access or model calls are made.
