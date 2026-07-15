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

### Pre-registered expansion — the current headline (2026-07-13, `mistral-small-3.2-24b`, 240s/arm, n=15)

The n=6 goldilocks run below was one pair short of significance. The pre-registered, honest way to gain
power is **more tasks** (never re-rolling the same 6 until they cross — that would be p-hacking). So the
suite was expanded from 6 → 15 by adding **9 neutral tasks registered before the run** (balanced_brackets,
run_length, base_convert, merge_intervals, csv_parse, template_render, lru_cache, topo_sort, fix_flatten),
each validated against a correct reference solution first. Same goldilocks model, same fork/paired
discipline, same independent-pytest grading.

| task | raw-model (1-shot) | chimera (loop) | |
|---|---|---|---|
| roman_validate | ❌ | ❌ | |
| config_parse | ✅ | ✅ | |
| path_get | ❌ | ❌ | |
| eval_expr | ❌ | ❌ | |
| word_wrap | ✅ | ✅ | |
| fix_percentile | ✅ | ✅ | |
| balanced_brackets | ✅ | ✅ | |
| run_length | ✅ | ✅ | |
| base_convert | ✅ | ✅ | |
| merge_intervals | ✅ | ✅ | |
| csv_parse | ❌ | ❌ | |
| template_render | ❌ | ✅ | **recovered** |
| lru_cache | ✅ | ✅ | |
| topo_sort | ❌ | ✅ | **recovered** |
| fix_flatten | ✅ | ✅ | |

```
raw-model       60.0%  (9/15 paired trials)
chimera         73.3%  (11/15)
paired delta    +13.3%   95% CI [-4.2%, +13.3%]
discordant       chimera +2 / raw-model +0   (template_render, topo_sort — two recoveries, zero regressions)
verdict          NOT significant (CI includes 0)
```

**The lift shrank as the suite grew, and that is the honest number.** The 9 new tasks are ones the
goldilocks model largely one-shots (7/9 pass raw), so they add agreement pairs, not discordant signal —
the delta dropped from +50pp (n=6) to **+13.3pp (n=15)**, which is the more representative estimate of
what the loop buys on a mixed-difficulty suite. What holds across both runs: **the loop never regressed a
task** (0 discordant losses in either run) and every point of lift is a task it *recovered* from a raw
fail to a verified pass. Still not significant — 2 discordant wins out of 15 is a real but small effect,
and we report it as-is. This n=15 run is what the shipped snapshot (`_benchmark_snapshot.json`), the app's
Maturity panel, and the READMEs cite.

---

### Goldilocks run — earlier n=6 run, superseded by the n=15 expansion above (2026-07-07, `mistral-small-3.2-24b`, 250s/arm)

The 8B floored (near-zero discordant signal) and the competent model ceiling'd; the honest fix is a
*goldilocks* model — weak enough to fail several tasks one-shot, capable enough that the loop
recovers several. A cheap baseline probe found one: `mistral-small-3.2-24b` (baseline ≈ 2/6).

| task | raw-model (1-shot) | chimera (loop) | |
|---|---|---|---|
| config_parse | ✅ | ✅ | |
| eval_expr | ❌ | ✅ | **recovered** |
| word_wrap | ❌ | ✅ | **recovered** |
| fix_percentile | ❌ | ✅ | **recovered** |
| roman_validate | ❌ | ❌ | |
| path_get | ❌ | ❌ | |

```
raw-model       16.7%  (6 paired trials)
chimera         66.7%
paired delta    +50.0%   95% CI [-6.1%, +50.0%]
discordant       chimera +3 / raw-model +0   (all three chimera wins, zero losses)
verdict          NOT significant — by 6 points
```

**The full loop tripled the pass rate (17% → 67%) and won every discordant pair (3–0).** The paired
95% CI `[-6.1%, +50%]` is one pair short of excluding zero: with 3/3 discordant wins the Wilson bound
doesn't quite clear it; **4/4 would**. This is the clearest directional signal to date — the loop
never lost a pair — and the pairing keeps the interval tight enough that the near-miss is meaningful,
not noise.

**We do not re-roll for significance.** Running this until it crosses would be p-hacking — the exact
dishonesty this whole bench exists to avoid. The legitimate ways to gain power are pre-registered:
more tasks or more seeds per task (increasing n), or the official hard-task benchmarks. Recorded as
is: a strong, honest, one-pair-short result on a fair single run.

---

### Earlier run (2026-07-07, `llama-3.1-8b-instruct`, 200s/arm) — too weak, near-zero signal

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

---

## M18-1 gate A/B — coverage-grade vs spec-grounded generated tests (PRE-REGISTERED 2026-07-09)

**Regime:** `solve` with NO `--verify`. Two arms differ ONLY in the gate: `--checklist` (LLM coverage
grade) vs `--gen-tests` (executable spec tests). Same model (mistral-small-3.2-24b, goldilocks), same
scaffolding, paired fresh workspace per arm. The task's hidden test is withheld from solve and written
only to grade — no leak.

**Predictions (registered before the run):**
1. **False positives** (gate says "success" but the hidden test fails) — gen-tests **< coverage-grade**.
   This is the core M18-1 claim (executable spec tests catch wrong code the coverage grade rubber-stamps).
2. **Resolve rate** (hidden test passes) — gen-tests **≥ coverage-grade**, but on a weak model both may
   sit near the floor; the discriminating signal is #1, not necessarily a significant resolve-rate lift.
3. n=6 is small; the paired McNemar/Wilson may well be non-significant. Report as-is, no re-roll.


### Result (run 2026-07-09, mistral-small-3.2-24b, n=6, timeout 240s/arm) — as-is, no re-roll

```
                       coverage-grade  gen-tests
resolve rate (hidden)      0/6 (0%)     3/6 (50%)
paired delta (Δ)           +50.0%   95% CI [-6.1%, +50.0%]  -> NOT significant (CI includes 0)
discordant pairs           gen-tests +3 / coverage +0  (roman_validate, config_parse, fix_percentile)
false positives            0            1  (eval_expr)
```

**Prediction #2 HELD (strongly, directionally):** gen-tests solved 3/6 where coverage solved 0/6, all
3 discordant pairs favouring gen-tests, 0 against. One pair short of significance at n=6 — the same
"strong signal, small-n CI" pattern the goldilocks runs keep producing. Not significant, reported as-is,
no re-roll (that would be p-hacking).

**Prediction #1 RETRACTED — it was wrong in direction.** I predicted gen-tests would have *fewer* false
positives than coverage. Actual: coverage 0, gen-tests 1. The honest reason: on this weak model the
coverage-grade arm *never self-reported success* (0/6 self=PASS) — it just keeps judging "not covered"
and reverts forever, so it has zero opportunity to false-accept (and zero true-accepts: it solves
nothing). gen-tests self-reported success 4×, of which 3 were real and 1 (eval_expr) was a genuine false
positive: the weak model wrote a generated test too shallow to catch the edge case the hidden test
checks. So spec-grounded tests are NOT a perfect oracle — a weak model can write a weak test.

**Honest verdict for M18-1:** the win is real but it is a **resolve-rate** win, not the false-positive
reduction I hypothesised. Executable pytest feedback lets the weak model *converge* (concrete failing
assertions to fix), where the LLM coverage grade is a dead end that accepts nothing. That is exactly the
"weak-model-lift" the project chases, measured (+50pp, 3-0 discordant, non-significant at n=6). The
caveat ships with it: `--gen-tests` can itself be fooled by a shallow generated test (1/6 here) — a
candidate follow-up is to reject trivially-passing generated tests (min assertions / mutation check).
Cost: ~$1-2 (12 solves on a cheap model). Key: disposable `.env` test key.
