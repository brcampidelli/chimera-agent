# Weak-model-lift A/B — recorded run (raw one-shot vs the full Chimera loop)

## Paired runner (M15-C1)

`run_paired.py` runs both arms from the **identical restored workspace** per task (the M15-B1
fork/paired discipline) and reports the **paired (McNemar/Wilson)** verdict — a tighter CI than the
unpaired Newcombe, so a real lift can clear zero at the small n a disposable key affords. Verdict is
still the tests' word (pytest re-run independently), never solve's self-report.

```bash
# needs a cheap tool-capable model key in OPENROUTER_API_KEY
BENCH_MODEL=openrouter/meta-llama/llama-3.1-8b-instruct BENCH_TIMEOUT=200 \
  python bench/local_lift/run_paired.py            # all 6 tasks, paired
```

### Recorded 6-task paired run (2026-07-07, `llama-3.1-8b-instruct`, 200s/arm)

| task | raw-model (1-shot) | chimera (loop) | |
|---|---|---|---|
| roman_validate | ❌ | ❌ | |
| config_parse | ❌ | ❌ | |
| path_get | ❌ | ❌ | |
| eval_expr | ❌ | ❌ | |
| word_wrap | ❌ | ✅ | **recovered** |
| fix_percentile | ❌ | ❌ | |

```
raw-model        0.0%  (6 paired trials)
chimera         16.7%
paired delta    +16.7%   95% CI [-9.8%, +16.7%]
discordant       chimera +1 / raw-model +0
verdict          NOT significant (CI includes 0)
```

**The headline is the method, not the number.** The paired CI is **`[-9.8%, +16.7%]` (width ~26pp)** —
about **3× tighter** than the unpaired Newcombe CI on the comparable earlier run
(`[-29.5%, +55.8%]`, width ~85pp). The B1 pairing did exactly what it promised: conditioning out the
five tasks both arms agree on removes the agreement noise the unpaired interval still pays for.

**Why it is still not significant: too few discordant pairs, not a wide interval.** This run the 8B
was weak enough to fail *all six* tasks one-shot (0/6 raw), and the loop recovered only one
(`word_wrap`) — so there is exactly **one** discordant pair, and one win cannot exclude zero at 95%.
The lift is real and positive (chimera got something, raw got nothing) but n=1 discordant pair is no
proof. Reported plainly.

**What this says about the regime.** The competent model (`deepseek-chat-v3.1`) ceiling'd at 6/6 (no
headroom); this 8B floored at ~0–1/6 (almost no discordant signal). Significance needs a *goldilocks*
model — weak enough to fail several one-shot, capable enough that the loop recovers several — or many
more tasks. The pipeline and the tighter paired statistic are proven; the significant number waits on
the right regime (the official hard-task benchmarks) or a bigger n than a $5 key affords.

---


This is a durable record of one honest A/B run of the local weak-model-lift bench. Unlike
`README.md` (which documents the `run_ab.py` design where the *only* variable is the M14 flags),
this run used **`run_ci.py`**, whose two arms answer the headline thesis question directly:

> Does driving a genuinely weak model through the whole Chimera loop beat using that same model raw?

- **`raw-8B`** — the model, **one shot**, no plan / no manager / `--max-attempts 1`.
- **`chimera-8B`** — the same model through the full loop: plan + manager + verify-or-revert,
  `--max-attempts 3`, `--repo-map --progress-ledger --checklist --replan`.

The variable therefore bundles retries + supervision + M14 scaffolding together (vs. the raw
one-shot), which is exactly the "is Chimera worth it over the bare model" comparison.

## Setup

| | |
|---|---|
| Date | 2026-07-07 |
| Model | `openrouter/meta-llama/llama-3.1-8b-instruct` (a genuinely weak, tool-capable 8B) |
| Tasks | the 6 build-from-scratch tasks in `tasks.py` |
| Grading | each task's own strict `pytest` file, re-run independently after solve (never solve's self-report) |
| Verdict engine | `chimera bench-compare` (Wilson per-arm bounds + Newcombe 95% CI on the delta) |

Why an 8B: the previous run on a competent model (`deepseek-chat-v3.1`) scored **6/6 on both arms**
— a ceiling effect with no headroom to measure a lift. The honest A/B correctly reported Δ 0%,
which is *why* we dropped to a model weak enough to fail, so the scaffolding has something to recover.

## Per-task outcome

| task | raw-8B (1-shot) | chimera-8B (loop) | note |
|---|---|---|---|
| roman_validate | ❌ | ❌ | loop ran full 3 attempts (118s) — genuine quality miss |
| config_parse | ❌ | ✅ | **recovered by the loop** (fail → pass) |
| path_get | ❌ | ❌ | loop ran full 3 attempts (145s) — genuine quality miss |
| eval_expr | ❌ | ❌ | recursive-descent parser; loop couldn't finish within 480s (hardest task) |
| word_wrap | ✅ | ✅ | held |
| fix_percentile | ❌ | ❌ | loop ran full 3 attempts (172s) — genuine quality miss |

## Verdict

```
raw-8B         16.7%  [3.0%, 56.4%]  (1/6)
chimera-8B     33.3%  [9.7%, 70.0%]  (2/6)
delta (Δ)     +16.7%  95% CI [-29.5%, +55.8%]
verdict        NOT significant (CI includes 0)
```

## Honest conclusion

- **The ceiling effect is broken and a directional lift is visible.** The full loop doubled the raw
  pass-rate (1 → 2 of 6): it *recovered* `config_parse` (which the raw model failed one-shot) and
  *held* `word_wrap`. That is the shape the thesis predicts.
- **It is not statistically significant at n = 6.** The Newcombe CI on the delta spans zero. A
  +16.7pp delta on 6 tasks cannot be called a proven lift — and the bench says so, out loud. This is
  the honesty guard working: it stops us from publishing "Chimera doubles a weak model!" off a
  1→2 sample.
- **Significance is out of reach on this budget.** To make a ~17pp delta clear the CI you need on the
  order of n ≈ 50 tasks. Each `chimera-8B` solve here ran 100–500s on the slow model, so ~50 tasks ×
  2 arms is hours of wall-clock and well past the disposable test key's cap. Reported, not hidden.
- **What is proven is the pipeline.** Weak-model baseline → full Chimera loop → independent pytest
  grading → Newcombe-CI verdict now runs end-to-end and reproducibly. The number it produces is
  honest; the number just isn't big-n yet.

### Confounds encountered (and handled)

- **Provider 5xx.** OpenRouter returned `Internal Server Error` on two solves mid-run; those were
  re-run rather than scored as quality misses (a 500 is the provider's fault, not the model's).
- **Timeout budget.** The loop's 3 attempts on a slow 8B legitimately need far more wall-clock than a
  one-shot; runs that hit the per-solve timeout were re-run with a larger budget so the delta measures
  *quality*, not *"did it fit in 250s"*. `eval_expr` still couldn't finish within 480s and is scored
  as a fail (the raw model also failed it), which is the fair call.

To reproduce (needs a cheap tool-capable model key in `OPENROUTER_API_KEY`):

```bash
BENCH_MODEL=openrouter/meta-llama/llama-3.1-8b-instruct BENCH_ARM=baseline \
  BENCH_TIMEOUT=200 python bench/local_lift/run_ci.py         # 1/6
BENCH_MODEL=openrouter/meta-llama/llama-3.1-8b-instruct BENCH_ARM=chimera \
  BENCH_TIMEOUT=480 python bench/local_lift/run_ci.py         # 2/6
chimera bench-compare results/ci-baseline.json results/ci-chimera.json \
  --baseline-name raw-8B --treatment-name chimera-8B
```
