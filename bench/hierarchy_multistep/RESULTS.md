# Results — multi-step hierarchy A/B (the token crossover)

Predictions registered in README.md before the run. This is the companion to
`bench/hierarchy` (single-shot), which honestly showed the hierarchy *loses* on
tokens. Here we test the regime where it should win.

## Run 1 — 2026-07-08, deepseek-chat-v3.1 (same model both arms), n=6

`BENCH_MODEL=openrouter/deepseek/deepseek-chat-v3.1 python bench/hierarchy_multistep/run.py`
(raw in `results/paired.json`)

| metric | single-context | scoped (hierarchy) |
|---|---|---|
| pass rate | 100% | 100% |
| paired Δ | — | +0.0% (identical) |
| total tokens | 236,373 | **79,181** |
| median tokens/task | 39,386 | 13,194 |
| **token reduction** | — | **+66.5%** |

Per task the baseline spends ~39.4k and the scoped arm ~13.2k — a **~3× ratio,
exactly Q** (3 sub-questions each re-sending 3 large docs vs each doc read once).
The measured ratio matches the `Q × Σdocs` vs `Σdocs` model precisely.

### Predictions — held?

| # | prediction | verdict |
|---|---|---|
| 1 | scoped ≥ 40% fewer tokens | **HELD** — 66.5% reduction |
| 2 | quality non-inferior | **HELD** — 100% vs 100%, no loss |
| 3 | reduction grows with Q / doc size | **consistent** — the measured ratio ≈ Q, as the model predicts |

### Honest reading

This is the mirror image of the single-shot result, and together they are the whole
honest story:

- **Single-shot, small docs** (`bench/hierarchy`): hierarchy costs **+47% MORE**
  tokens — fan-out overhead with nothing to amortize.
- **Multi-step, large docs** (here): hierarchy costs **−66% FEWER** tokens — because
  the single agent re-sends every document on every turn while scoped workers read
  each document once.

The token economy of orchestration is **real but regime-specific**. That is exactly
why `chimera orchestrate` gates on task shape and a profitability estimate instead of
always fanning out: it should engage precisely in the multi-step/large-context regime
this bench isolates, and stay out of the single-shot regime the other bench isolates.

### Caveat we will not hide

Token counts are real, but with **prompt caching** a provider bills the baseline's
repeated document prefix at ~0.1×. So the *dollar* reduction on a cache-friendly
provider is smaller than 66.5% — the tokens are saved (latency, context-window
pressure, and non-cached providers all benefit fully), but a cache-aware cost model
narrows the billing gap. We measure tokens and say this plainly; we do not claim a
dollar figure we did not measure.
