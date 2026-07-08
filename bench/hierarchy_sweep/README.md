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
|---|---|---|---|---|
| 2 | 17,608 | 8,800 | **+50.0%** | 50.0% |
| 3 | 39,487 | 13,187 | **+66.6%** | 66.7% |

**The law holds to <0.1%.** D=4 and D=5 did not complete — the capped OpenRouter key hit
its total spend limit (403) mid-run. They're left for a future run with headroom; the
offline unit test (`tests/test_hierarchy_multistep.py::test_sweep_reduction_scales_with_doc_count`)
already proves the trend continues monotonically to D=5 under a char-cost model.

### Honest reading

The measured `(D−1)/D` fit confirms the mechanism precisely: the hierarchy's token
economy is a **context-isolation** effect — the single agent re-sends all D documents
on every turn, the scoped workers each read one. The more documents in play, the larger
the win. This is the same effect the single-shot bench lacked (D docs, but only ONE turn,
so nothing to re-send → the fan-out overhead lost). Two benches, one curve, one honest
mechanism. Caveat unchanged: prompt caching narrows the DOLLAR gap; we measure tokens.

### S and Q axes — offline-proven, real run pending

The S and Q sweeps ship with their offline trend tests green; the real-model runs are
pending a key with spend headroom (the capped key hit its limit mid-run). Expected shape,
from the model and the offline tests: S rises from a smaller win at tiny docs toward
(D−1)/D as docs grow (never inverting); Q stays flat near (D−1)/D. Fill these in with the
measured rows once a fresh key is in `.env`.
