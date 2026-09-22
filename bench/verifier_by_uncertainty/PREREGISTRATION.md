# Pre-registration — the strong verifier fired by uncertainty, with the signal that survived

*Written 2026-09-22, before `run.py` produced a number. Study 20 §3 C2 (`bench/PLAN-study20-calibrated-decisions.md`), after study 21 Tier B4 (`bench/jev_decisions/RESULTS.md` §12) measured the claim-vs-diff Noul at chance (AUROC 0.54 [0.42, 0.66]) and took it out of the combination. What is left is the lexical signal: `overlap` from `bench/claim_vs_diff` (0.6643 [0.532, 0.792] within task).*

## 1. The question, and why it is smaller than C2 wrote it

C2 asked for `p(true_success)` combined from three readings — the lexical overlap, the verifier state, and P(APPROVED) from the Manager once logprobs exist — and for the strong verifier (D9, `chimera/core/autonomous.py:1176`) to fire when that `p` sits in an uncertain band, measured against what the shipped trigger spends. Two of the three readings do not exist on this corpus (`verify_output` is empty on 547 of 547; the Manager's logprobs were not recorded), and the B4 Noul is at chance. So the question this bench can answer is:

> **Does firing D9 on the lowest `overlap` scores catch more false successes than the shipped trigger, at the same number of D9 calls — and than flagging at random, at the same budget?**

## 2. Corpus

The 547 factorial solves (`bench/harness_bench`, `~/hb-homes`), read by `bench/claim_vs_diff/claims.py`: 385 claimed successes, of which 139 are false by the oracle. Ten askable tasks. Nothing is spent — every score is computed from the receipt, the diff and the trace already on disk.

## 3. The shipped trigger, read off the receipts first

D9 fires on `index > 1`: only an attempt that follows a failed one is verified. **Q0** counts, among the 385 claimed successes, how many were attempt 2 or later. The receipts already say what the answer is — `attempts` has length 1 on every run I have looked at while writing this — so the prediction is stated as a prediction and the count is still printed:

- **P0.** The shipped trigger covers **0 of 385** claimed successes. The factorial ran single-attempt, so on this corpus D9 spends 0 calls and catches 0 of 139. That is a fact about the corpus (§2q: it cannot show what the trigger does under retries) AND a fact about the gate's design: a deployment where the first attempt claims success — the common case — never has that claim verified by D9, whatever the signal.

## 4. Arms

All ranked **within task**, leave-one-task-out (the `overlap` arm is fitted on the other nine tasks and scores the held-out one), exactly as `bench/claim_vs_diff/measure.py` did — so the counts here must reproduce that file's `catch_at_flag_rate` (§7 control).

| arm | fires D9 on |
|---|---|
| `shipped` | claimed successes with `index > 1` — **0 calls** on this corpus (§3) |
| `overlap_low` | the lowest-`overlap` fraction *b* of each task's claimed successes (most suspicious first) |
| `overlap_band` | the *middle* fraction *b* — the C2 wording ("fire when p is uncertain"), kept so the band idea is measured and not assumed: items ranked between the *b*/2 quantile and the 3*b*/2 quantile of each task |
| `random` | a fraction *b* of each task's claimed successes, uniformly — the **expectation** Σ_t take_t · pos_t / n_t, not one draw |

Budgets *b* ∈ {10%, 20%, 30%}; take_t = max(1, round(b · n_t)) per task, as published.

## 5. Primary measurement

At each budget: **extra false successes caught** = catch(`overlap_low`) − catch(`random`), out of 139, with a **95% cluster bootstrap over tasks** (resample the ten tasks with replacement, 2000 draws, seed 20260913) and a **within-task label permutation** null (2000 shuffles of the labels inside each task; the fraction of shuffles whose `overlap_low` catch ≥ the observed one is the p-value).

Cost is printed beside it: D9 calls = Σ_t take_t, and **calls per extra catch**.

## 6. Decision rule — written before the run

**ADOPT** (replace `index > 1` by "fire D9 on the lowest-overlap fraction *b*") only if, at *b* = 30%:

1. the 95% CI of (`overlap_low` − `random`) **excludes 0**, and the permutation p ≤ 0.05; **and**
2. **calls per extra catch ≤ 10** — one false success caught per ten D9 calls is the most a verifier call costing a full model pass can be worth on a run where the diff-gate is already free.

**Otherwise: publish the null and D9 stays as it is** — the reading Bruno fixed on 2026-09-22 ("se não bater, publica-se o nulo e o D9 fica como está").

The published point estimates (48 against ≈42 at 30%, `bench/claim_vs_diff/RESULTS.md` §3) are already known while this is written: +6 of 139 for 115 calls is **19 calls per extra catch**, which fails (2) on its face. What is *not* known, and is the new measurement, is whether +6 is distinguishable from noise at all (1), and whether the band (`overlap_band`) does anything the bottom does not. Both are registered as predictions:

- **P1.** The 95% CI of (`overlap_low` − `random`) at 30% **includes 0**. The effect is inside the task-level noise of a ten-task corpus.
- **P2.** `overlap_band` catches **fewer** than `overlap_low` at every budget — the uncertain middle of a weak ranker is not where the false successes are; the bottom is.
- **P3.** Neither condition of §6 is met; verdict **null**.

## 7. Controls

- **positive:** an arm fed the oracle score, ranked the same way, catches every false success once *b* reaches each task's false-success rate — printed as the ceiling at each budget; and the AUROC control from `claim_vs_diff` must still read exactly 1.000.
- **paired:** `overlap_low` at 10/20/30% must reproduce the published **13/139 · 30/139 · 48/139** exactly; `random` must reproduce ≈42 at 30%. If either differs, the apparatus changed and nothing here is a comparison.
- **negative:** with labels shuffled within task, `overlap_low` − `random` must sit at ≈0 (its distribution is the permutation null of §5).

## 8. What this cannot show (§2q)

- The shipped trigger under **retries** — the corpus has none. A factorial with `max_attempts ≥ 2` is what it would take to compare `index > 1` on its own terms.
- Anything about the verifier's own accuracy: D9's verdicts were never recorded on these solves (no bench, as C2 already said). This bench measures *which items would be sent*, not what D9 would say.
- A calibrated `p(true_success)`: the overlap score is a logistic decision value, not a probability, and no map exists for it. "Uncertain band" here is a quantile band, not a probability band.
