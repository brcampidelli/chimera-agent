# Hierarchy paired A/B — single-agent vs orchestrator-worker, same model

Does the orchestrator-worker split (contracts + minimal-context scoping +
budgets) beat a single agent on **read-heavy multi-part tasks** — on quality
AND tokens? Both arms run the SAME mid model, so the comparison isolates the
orchestration, not model strength.

## Registered predictions (written BEFORE any run — wins or losses go in RESULTS.md)

1. **Token reduction ≥ 30%** on the synthetic read-heavy suite (baseline carries
   every document inline; hierarchy workers each see only their own document).
2. **Non-inferior quality**: the paired delta's 95% CI does not show a
   significant LOSS for the hierarchy (McNemar/Wilson; "significant" is used
   only on this quality axis — never on cost).
3. **Negative control** (`bench/local_lift` coding tasks): the deterministic
   classifier falls back to single-agent on ALL of them (`sequential_write`) —
   the hierarchy must not engage where the evidence says it loses. This is
   asserted in the unit tests (`tests/test_hierarchy_ab.py`) as well.

## Arms

| arm | what it sees | calls |
|---|---|---|
| `single-agent` (baseline) | ALL documents inline + full question | 1 |
| `hierarchy` (treatment) | one budgeted worker per document (its doc ONLY) + top-tier synthesis over bounded summaries | k workers + 1 synthesis |

Grading is independent of both arms: each task plants deterministic facts, and
ALL fact needles must appear in the final answer.

## Run

```bash
export OPENROUTER_API_KEY=sk-or-...
export BENCH_MODEL=openrouter/deepseek/deepseek-chat-v3.1   # worker + baseline model
# optional: BENCH_TOP_MODEL for a different synthesizer; BENCH_TASKS=releases,vendors
python bench/hierarchy/run_paired.py
```

Outputs the paired quality verdict + the measured token table, and writes
`results/paired.json`.

## Honesty rules

- Quality verdict: paired McNemar/Wilson — significance claims live there only.
- Tokens: measured totals/medians; **no significance testing on cost**.
- Providers that report no usage fall back to a chars/4 estimate — the same
  estimator in both arms, so the comparison stays symmetric.
- One run, reported as-is; no re-rolling for a better p-value.
