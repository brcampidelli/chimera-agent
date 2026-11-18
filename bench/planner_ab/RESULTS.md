# Results — planner off is NOT adopted: one of two families fails the 5 pp margin

Run 2026-11-16 against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its Amendment 1. Reader:
`read_ab.py` (closed-form 90% t interval over the 23 per-task differences, margin -5 pp on the pass
rate = oracle `outcome_score >= 0.8`; unchanged from the registration).

| executor | family | solves | pass rate plan | pass rate no-plan | no-plan minus plan (pass) | 90% t interval | non-inferior (> -0.05) | tasks worse / equal / better | cost no-plan / plan |
|---|---|---:|---:|---:|---:|---|---|---|---:|
| E1 `deepseek-v3.2` (re-read of the stored factorial, arms 000 vs 001) | DeepSeek | 138 | 0.551 | 0.609 | +0.058 | [-0.020, +0.136] | yes | 2 / 16 / 5 | 0.971 (meter) |
| E2 `glm-5.3-flash` (new, 138 solves, 12-solve pilot kept) | Zhipu | 138 | 0.638 | 0.623 | -0.014 | [-0.082, +0.053] | **no** | 4 / 16 / 3 | 0.847 (token-priced) |
| E3 `qwen3-coder-next` | Alibaba | not run | | | | | | | |

Continuous score, same pairing (secondary): E1 +0.013 [-0.025, +0.052]; E2 -0.024 [-0.072, +0.024].
Both executors passed the validity gate (plan-arm pass rate in 0.30-0.95, 100% of solves scored).

## Verdict by the registered rule

Condition 1 (two families): met. The recorded cost ratios were within condition 3's limit (see
the accounting correction below). **Condition 2 fails**: E2's lower limit is **-0.082**, below
-0.05. **NOT ADOPTED.** The
point estimate for E2 is -1.4 pp, inside the margin; the interval is wide because a binary outcome over
23 tasks cannot resolve 5 pp (registration section 4 said so: half-width about 0.055-0.07; measured
0.067). That is "not shown", not "shown worse". `PLANNER_OFF_WHEN_VERIFIED` stays `False`; `use_planner`
is exposed and opt-in on lanes, the app run and workflow steps.

## E3 was not run, as registered

The 12-solve pilot (`results/qwen-pilot-not-analysed.jsonl`) was excluded from the effect analysis. The recorded
cost was US$ 0.0244 per solve at list price; 138 x 0.0244 x 1.25 = US$ 4.21, against a remaining ceiling of
5.00 - 1.71 - 0.50 = US$ 2.79, as reported at the time. Section 6 makes E3 all-or-nothing, so it
was dropped. **Correction:** all 12 pilot solves exited with rc = 0 and had oracle scores; four
were missing Chimera receipts.

## Cost, real money

The recorded estimate was **US$ 1.71** for E2 (138 solves) plus the E3 pilot and about US$ 0.05 for
the discarded first pilot, or **about US$ 1.8** against the US$ 5.00 ceiling. The driver valued the
receipt tokens at list price, with no cache discount and a 1.25 safety multiplier. The Chimera
meter used fallback prices for these models (Amendment 1), so its `usd` was recorded separately.

**Accounting correction:** the old driver omitted receiptless solves from spend, including one E2
solve and four E3 pilot solves. The single-model CLI planner also bypassed the receipt meter and
the per-run cap. The committed logs contain aggregate amounts for each solve, without the omitted
planner calls or their token counts. The actual spend, E2 cost ratio and E3 projection cannot be
reconstructed from those rows. The amounts above describe the recorded accounting. E2 still
fails the pre-registered pass-rate margin, which is sufficient to retain the default.

## What this cannot show

`--max-attempts 1`, no `--verify` in the solve, 23 coding tasks, two families. Nothing about the retry
path, runs with a live verifier, or non-coding turns. E1 was already published (aggregate +0.003 on the
score) and is not fresh evidence; only the pass-rate reading and the margin are new. A third family
would be needed to say more, and a design with more tasks (a margin of 5 pp needs a half-width under
0.05) to get a decision either way.

The measured pass-rate difference without the planner was +5.8 pp on E1 and -1.4 pp on E2. The
default stays on per the registered rule. Callers can still set `use_planner=false` for a run.
