# Multi-step hierarchy A/B — the token-crossover regime

The single-shot suite (`bench/hierarchy`) honestly showed the hierarchy **cannot**
win on tokens: one baseline call carries all documents once, so fan-out only adds
overhead. This companion tests the regime the literature actually credits —
**multi-step** work over **large** documents — where a single agent re-sends its whole
context on every turn.

## Setup

- 6 tasks, 3 large documents each (~5-6k chars/doc), one sub-question per doc (Q=3).
- **baseline (single-context):** one growing conversation; all 3 docs enter on turn 1
  and are re-sent on every one of the 3 sub-question turns → pays ≈ `Q × Σdocs`.
- **scoped (hierarchy):** each sub-question routed to a worker seeing ONLY its doc →
  pays ≈ `Σdocs` (each doc once).
- Same model both arms (isolates context scoping). Deterministic planted-needle
  grading, ALL must appear. Quality = paired McNemar/Wilson; tokens = measured totals.

Run: `BENCH_MODEL=openrouter/deepseek/deepseek-chat-v3.1 python bench/hierarchy_multistep/run.py`

## Predictions — registered BEFORE the first run (2026-07-08)

1. **Token crossover appears: scoped uses materially fewer tokens than single-context**
   (target: ≥ 40% reduction). This is the opposite of the single-shot suite, and the
   whole reason the regime matters.
2. **Quality non-inferior** — no significant quality loss from scoping (paired).
3. The reduction is driven by the re-sent-docs term, so it should **grow with doc size
   and with Q** (not asserted numerically here — a direction, for a future sweep).

## Honesty footnote (carried into RESULTS)

Token counts are real, but a provider with **prompt caching** bills the baseline's
repeated document prefix at ~0.1×, so the *dollar* gap is narrower than the *token*
gap. We measure tokens (which also govern latency and context-window pressure), never
claim cost significance, and say this out loud. The dollar crossover still exists —
it just needs a larger Q or non-cacheable churn to dominate.

Results in `RESULTS.md`.
