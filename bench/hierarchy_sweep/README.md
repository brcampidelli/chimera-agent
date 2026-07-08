# Token-crossover sweep — the hierarchy's saving across three axes

`bench/hierarchy` (single-shot) showed a LOSS; `bench/hierarchy_multistep` (multi-step)
showed a WIN. This sweep varies the knobs that drive the effect and measures where the
saving lands, turning isolated points into curves. Same model both arms; deterministic
planted-needle grading; measured token totals (no cost-significance claim).

Run: `BENCH_AXIS=D|S|Q BENCH_MODEL=... python bench/hierarchy_sweep/run.py`
(env `BENCH_POINTS`, `BENCH_FIXED_D`, `BENCH_FIXED_REPS`)

## The three axes (predictions registered before running)

- **D — number of documents** (`BENCH_AXIS=D`, Q=D). Every turn re-sends all D docs;
  scoped workers read one each → saving ≈ **(D−1)/D** (50% @ D=2, 67% @ D=3, 75% @ D=4…).
  The context-isolation win grows with how many docs a single agent must juggle.
- **S — document size** (`BENCH_AXIS=S`, fixed D=3). Tiny docs → the fixed per-call
  framing (system prompts, questions) is a bigger slice → smaller win; big docs → the
  doc dominates → win rises toward (D−1)/D. **Rises with S but does not invert** in this
  multi-turn design — the true loss regime is the single-shot bench (fan-out + synthesis).
- **Q — conversation length** (`BENCH_AXIS=Q`, fixed D=3). Both arms scale ~linearly in
  Q, so the win is **roughly flat at (D−1)/D** — a stability check that the win holds as
  sessions get long, not a lever.

Each trend is locked by an offline unit test (char-cost backend) in
`tests/test_hierarchy_multistep.py` (`test_sweep_reduction_scales_with_doc_count`,
`test_S_axis_win_rises_with_doc_size_toward_the_limit`, `test_Q_axis_win_is_roughly_flat`).

## Result — D axis, 2026-07-08, deepseek-chat-v3.1

| D | baseline tokens | scoped tokens | reduction | (D−1)/D |
| D | baseline | scoped | reduction | (D−1)/D |
|---|---|---|---|---|
| 2 | 17,555 | 8,800 | **+49.9%** | 50.0% |
| 3 | 39,651 | 13,188 | **+66.7%** | 66.7% |
| 4 | 69,955 | 17,604 | **+74.8%** | 75.0% |
| 5 | 109,220 | 21,992 | **+79.9%** | 80.0% |

**The (D−1)/D law holds across all four points to <0.2%.** (`results/d.json`)

## Result — S axis (doc size, fixed D=3, Q=3), 2026-07-08

| S (filler reps) | baseline | scoped | reduction |
|---|---|---|---|
| 4 | 4,464 | 1,524 | +65.9% |
| 10 | 10,261 | 3,469 | +66.2% |
| 20 | 19,965 | 6,726 | +66.3% |
| 40 | 39,465 | 13,189 | +66.6% |
| 80 | 78,261 | 26,154 | +66.6% |

The win rises with doc size (65.9% → 66.6%) toward the (D−1)/D = 66.7% limit as the fixed
per-call framing tax shrinks — and, as predicted, **does not invert**: this multi-turn
baseline always carries D× the docs. (`results/s.json`)

## Result — Q axis (turns, fixed D=3), 2026-07-08

| Q (turns) | baseline | scoped | reduction |
|---|---|---|---|
| 2 | 26,278 | 8,791 | +66.5% |
| 3 | 39,483 | 13,200 | +66.6% |
| 4 | 52,719 | 17,582 | +66.6% |
| 6 | 79,351 | 26,379 | +66.8% |

**Flat at (D−1)/D across Q** — the win is stable as the conversation lengthens (both arms
scale ~linearly in Q). Confirms it's a robust property, not an artifact of a short chat.
(`results/q.json`)

### Honest reading

All three axes match their registered predictions on real runs. The `(D−1)/D` fit confirms
the mechanism precisely: the hierarchy's token economy is a **context-isolation** effect —
the single agent re-sends all D documents on every turn, the scoped workers each read one.
The win scales with D (more docs = bigger win), rises with doc size toward the same limit,
and holds flat across conversation length. This is the same effect the single-shot bench
lacked (D docs, but ONE turn → nothing to re-send → the fan-out overhead lost). Three axes,
one curve, one honest mechanism. Caveat unchanged: prompt caching narrows the DOLLAR gap; we
measure tokens.
