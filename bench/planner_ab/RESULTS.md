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

Condition 1 (two families): met. Condition 3 (no-plan not dearer by > 5%): met, no-plan is 3% and 15%
cheaper. **Condition 2 fails**: E2's lower limit is **-0.082**, below -0.05. **NOT ADOPTED.** The
point estimate for E2 is -1.4 pp, inside the margin; the interval is wide because a binary outcome over
23 tasks cannot resolve 5 pp (registration section 4 said so: half-width about 0.055-0.07; measured
0.067). That is "not shown", not "shown worse". `PLANNER_OFF_WHEN_VERIFIED` stays `False`; `use_planner`
is exposed and opt-in on lanes, the app run and workflow steps.

## E3 was not run, as registered

Its 12-solve pilot (kept at `results/qwen-pilot-not-analysed.jsonl`, never read for an effect) cost
US$ 0.0244 per solve at list price; 138 x 0.0244 x 1.25 = US$ 4.21, against a remaining ceiling of
5.00 - 1.71 - 0.50 = US$ 2.79. Section 6 makes E3 all-or-nothing, so it was dropped. 4 of its 12 pilot
solves were rc != 0 or without an oracle score (not investigated; the executor is excluded either way).

## Cost, real money

Token-priced at list price (no cache discount), x1.25 safety, from receipts: **US$ 1.71** for E2 (138
solves) plus the E3 pilot, plus about US$ 0.05 for the discarded first pilot. **About US$ 1.8 of the
US$ 5.00 ceiling.** The chimera meter priced these models at about 39x too high (Amendment 1), so its
`usd` is recorded but not used.

## What this cannot show

`--max-attempts 1`, no `--verify` in the solve, 23 coding tasks, two families. Nothing about the retry
path, runs with a live verifier, or non-coding turns. E1 was already published (aggregate +0.003 on the
score) and is not fresh evidence; only the pass-rate reading and the margin are new. A third family
would be needed to say more, and a design with more tasks (a margin of 5 pp needs a half-width under
0.05) to get a decision either way.

The recommendation to the owner is unchanged from `harness_bench`: for verified code tasks the planner
is not shown to help (E1 +5.8 pp without it, E2 -1.4 pp) and costs 3-15% more; turn it off per run with
`use_planner=false` where wanted. The default does not change on this evidence.
