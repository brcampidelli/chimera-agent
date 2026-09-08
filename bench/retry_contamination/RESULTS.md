# Is a retry an independent draw? — the second attempt is not, and the third is too small to read

Item 1+7, Step 0 of [`../PLAN-study16-eight-axes.md`](../PLAN-study16-eight-axes.md), run 2026-09-08
over this desktop install's own `runs.jsonl` (88 receipts, 2026-08-27 → 2026-09-07). The question
comes from arXiv **2605.08563**: retries of a coding agent are not independent draws — the IID model
`pass@k = 1 − (1 − p)^k` overestimated pass@3 by **17.4 pp**. If that holds here, `max_attempts=3`
is budgeted on a formula that is wrong in a known direction, and the receipts can say so for US$ 0.

**Bottom line.** The plan's hand count reproduced exactly (4/21, 0/11). The third attempt is
*suggestive, not decisive* — p≈0.10 at n=11, and zero recoveries cannot reach p<0.05 below n=15.
The unplanned finding sits one row up: **the second attempt recovered 4 of 21 where independence at
the first-attempt rate predicts 12.5** (exact tail 0.0002). And the money sentence in the plan is
backed with a number: **US$ 2.48 of the US$ 15.70 this install has spent — 15.8% — went to third
attempts, none of which succeeded.**

## 0 · Prediction — written before the probe ran

Registered on 2026-09-07 in the plan, from a hand count of the same file:

| row | prediction |
|---|---|
| 2nd attempt after a failed 1st | 4 succeeded / 17 failed (19%) |
| 3rd attempt after a failed 2nd | 0 succeeded / 11 failed |
| IID at p≈0.19 | ~2 of 11 third attempts recover; P(0 of 11) = 0.81^11 ≈ 0.098 |
| money | *"`max_attempts=3` is paying for a third attempt this install has never seen succeed"* — no share was registered; the claim is qualitative and must be backed by a number or retracted |

Stated for honesty: writing the probe required inspecting the receipt schema, and that inspection
printed the per-attempt outcomes and costs before this section was typed. The prediction is the
plan's, fixed the day before; what was computed only after it was written are the derived
quantities — the binomial tails, pass@k and its bracket, the money shares, the distinct-task counts.

## 1 · How the receipt records an attempt's outcome — recorded, not inferred

Each line of `runs.jsonl` is a `RunReceipt` (`chimera/api/runs.py`) with an `attempts` list, and
**every attempt carries its own `success` boolean**, beside `verified`, `reverted`, `evidence` and
`usd`. It is the verify-or-revert verdict for that attempt. So per-attempt outcome is a recorded
fact here, not something read off run-level success and attempt position. Three things are *not*
recorded, and they bound the probe:

| not on the receipt | consequence |
|---|---|
| `max_attempts` — **0 of 88** carry it | which runs were *allowed* a k-th attempt is inferred (§3) |
| `ending` — absent on all 88 (they predate #368) | the loop's own ending vocabulary is unavailable; `stopped_reason` is the only cap signal |
| a verdict for a capped attempt | a run stopped by the spend ceiling records its last attempt as a *partial* one (`_partial_attempt`): `success: false`, never judged. Three such attempts exist; the probe marks them **cut** and shows every number with and without them |

Population: 88 lines, all readable; **4 have no attempt at all** (3 crashed, 1 empty; no price);
**84 runs with attempts, 40 distinct tasks, 19 distinct models**. One install, one user's work.

## 2 · The attempt-conditional table, and what independence predicted

`n` = runs that reached attempt k with every earlier attempt failed. `p_prev` = the previous row's
observed rate. `P(≤obs)` = exact binomial lower tail of the observed count under `p_prev`.

| k | n | succeeded | failed | cut | distinct tasks | rate | p_prev | IID expects | P(≤obs) | judged-only |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 84 | 50 | 34 | 0 | 39 | 59.5% | — | — | — | — |
| 2 | 21 | **4** | 17 | 2 | 13 | 19.0% | 0.595 | **12.5** | **0.0002** | 0.0007 (4/19) |
| 3 | 11 | **0** | 11 | 1 | 10 | 0.0% | 0.190 | 2.1 | **0.098** | 0.094 (0/10) |

Both hand counts reproduced to the digit. Two readings follow, in decreasing order of strength.

**The third attempt (the registered row): suggestive, not decisive.** 0 of 11 where independence
at 0.19 expects 2.1 is p = 0.098 one-sided — the direction the paper predicts, at a size that
cannot decide. Zero recoveries would need **n ≥ 15** to reach p < 0.05 at that rate; this install
has 11. The judged-only number (one third attempt was cut by the ceiling, so 0 of 10 at 4/19) is
0.094. The 11 runs cover **10 distinct tasks**, so the n is nearly honest.

**The second attempt (not registered): far below independence, with a caveat that cannot be
closed from the receipt.** 4 of 21 recovered where the first-attempt rate, 59.5%, predicts 12.5;
the exact tail is 0.0002. For 4/21 to be ordinary, the true first-attempt rate *of the runs that
were allowed a retry* would have to be **≤ 0.384**, i.e. at most 13 of the 50 first-attempt
successes could have come from runs launched with `max_attempts ≥ 2` — the other 37 would have to
be single-attempt launches (the cron path without a verifier). The receipt cannot say which launch
path a run took, so this cannot be *ruled out*; it is not plausible on the app's paths (Code screen
and launcher default to 3, cron to 1 or 2), and roughly ten of the fifty look like daily scheduled
runs. Either way the operational statement holds: `1 − (1 − p)^k` at the pooled rate is wrong here,
whether because retries are contaminated (the paper's mechanism) or because the retried population
is much harder than the pool (task heterogeneity) — for budgeting attempts the two are the same
error. The 4 recoveries came from **3 distinct tasks**.

**The n behind the n (§2p).** The 21 retried runs cover 13 tasks, and three tasks account for 11 of
the 21 runs: one task was launched four times with two attempts each and failed every time with an
identical first-attempt cost (the same scheduled job, re-run); one was launched four times and
**recovered at attempt 2 twice and failed all three attempts twice** — the same task, both
outcomes, which is the task-level flip `chimera/eval/replicated.py` exists to report; one was
launched three times and stopped at the spend ceiling all three times.

## 3 · pass@k against the IID model — bracketed, because `max_attempts` is not on the receipt

`eligible` = runs that passed within k attempts or reached attempt k (either proves the run was
allowed k). Runs that failed and stopped earlier with no ceiling had a smaller cap (`exhausted<k`)
and are excluded; runs the ceiling stopped earlier (`cut<k`) are listed apart. Excluding them
biases the observed number **upward** — against the contamination reading — so the floor beside
it is the number if every excluded run had been allowed k and failed. The truth is between.

| k | eligible | passes | observed | floor | IID 1−(1−p1)^k | IID overshoot | exhausted<k | cut<k |
|---|---|---|---|---|---|---|---|---|
| 1 | 84 | 50 | 59.5% | 59.5% | 59.5% | 0 | 0 | 0 |
| 2 | 71 | 54 | 76.1% | 64.3% | 83.6% | **7.6 – 19.3 pp** | 13 | 0 |
| 3 | 65 | 54 | 83.1% | 64.3% | 93.4% | **10.3 – 29.1 pp** | 17 | 2 |

p1 = 50/84 over first attempts, which every run was allowed. The IID prediction lies **above the
whole bracket** at both k, so the selection ambiguity does not rescue it. 2605.08563's 17.4 pp at
k=3 sits inside our bracket; the receipts cannot narrow it further.

## 4 · The money

Every attempt in the file is priced (0 unpriced), so the shares are exact. The 4 runs without an
attempt carry no price and are outside the total.

| | USD | share of US$ 15.70 |
|---|---|---|
| attempt #1 (84) | 9.27 | 59.0% |
| attempt #2 (21) | 3.95 | 25.2% |
| **attempt #3 (11)** | **2.48** | **15.8%** |
| attempts the ceiling cut before any verdict (3: two 2nd, one 3rd) | 1.83 | 11.7% |

**The plan's sentence is backed, not retracted:** 15.8% of everything this install has spent bought
eleven third attempts across ten distinct tasks, and none succeeded. It is concentrated: six third
attempts above US$ 0.22 account for US$ 2.45 of the 2.48; the other five cost under US$ 0.007 each.

⭐ **A finding about the cap, not about retries.** The three cut attempts — US$ 0.19, US$ 0.86,
US$ 0.79 — each made **zero tool calls** and consumed 133k, 756k and 706k prompt tokens before the
spend ceiling fired. A retry that talks until the money runs out and never touches a file is
US$ 1.83 of this file, and no retry policy addresses it; the ceiling's placement does.

## 5 · What this cannot show

- **One install, one user, eleven days, 19 models.** Tasks were chosen in the course of work, and
  the retried ones are re-launches after failure — a population selected toward hard tasks, which is
  precisely the population a retry policy serves, and nothing like a benchmark.
- **The launch-path mix is invisible**, so the second-attempt row cannot separate "retries are
  contaminated" from "runs allowed a retry are a harder population". §2 states the rate that would
  make the row ordinary; the receipt cannot rule it out.
- **The verdicts are not all the same verdict.** 14 of the 21 retried runs, and 9 of the 11 third
  attempts, ran without a verifier command — their `success` is the diff-and-manager route, an LLM
  or a diff gate deciding, not a test. A manager that never approves a retry would produce this
  table without any contamination. Not separable at this n (the two verifier-judged third attempts
  also failed).
- **Escalation never acted.** Every retried run used the same model on every attempt; the plan's
  unconditional escalation (`escalate_worker`) was not configured on this desktop, so this says
  nothing about escalated retries.
- **n=11 on the registered row.** Stated above and again here: p≈0.10 is the direction, not the
  decision. The decision waits for the k-run protocol (item 0 of the plan), where each task is run
  k times and the third-attempt rate is measured rather than collected.

## 6 · What it changes

Nothing in the loop. The plan's claim stands with its number attached; whether `max_attempts`
should be 2 is a decision for a measurement, not a probe. One thing the next measurement should
not have to bracket: **`max_attempts` belongs on the receipt** — one field on `RunReceipt` — so the
population for pass@k is recorded instead of inferred. Not done here; the probe reads the field if
it is ever present.

## Reproduce

```
uv run python bench/retry_contamination/run_probe.py --receipts "%APPDATA%\app.chimera.desktop\data\runs.jsonl"
CHIMERA_RECEIPTS=/path/to/runs.jsonl uv run python bench/retry_contamination/run_probe.py
uv run pytest -q tests/test_a_retry_is_not_an_independent_draw.py
```

The receipts are **not committed** — they hold task text from one user's projects. The statistics
live in `chimera/eval/retries.py` (binomial tail by hand with `math.comb`; no scipy); the runner
prints counts and money only.
