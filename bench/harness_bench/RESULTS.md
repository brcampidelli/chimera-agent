# Results — none of the three harness factors beats the noise floor on the oracle, and each costs 10–16%

Run 2026-09-12/13 against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its amendments (model
`deepseek-v3.2`; 078/088 dropped for the public-tunnel prerequisite → **23 tasks × 8 arms × k=3 = 552
solves**). Total spend **US$ 29.28** on 552 solves (an order of magnitude under the US$ 400 stop, and under the ~US$ 205
v3.2 projection). Reader: `read_results.py`; per-solve rows in `~/hb-driver.jsonl`, oracle scores in
the harness's `data_try6/results/<arm>/…`.

## Verdict: a null with power — simplify, because none of the scaffolding earns its cost

Main effects on the oracle's `outcome_score`, paired by task, bootstrap 95% CI over the 23 tasks:

| factor | Δ (with − without) | 95% CI | vs noise floor |
|---|---:|---|---|
| **A · repo-map** | **−0.012** | [−0.036, +0.010] | inside (|Δ| < SD) |
| **B · checklist** | **+0.005** | [−0.052, +0.052] | inside |
| **C · planner** | **+0.003** | [−0.022, +0.029] | inside |

**Noise floor:** mean within-cell SD **0.073** (184 cells with k≥2); thresholded (≥0.8) flip rate 0.25.
Every effect is smaller in magnitude than the replica-to-replica noise, and every CI includes zero.
This is not an underpowered null: the SD is 0.073, so the design could see an effect of ~0.07+, and
there is none. On this model and these 23 coding tasks, **repo-map, checklist and the planner do not
move the outcome.**

> **2026-09-15, two amendments to the paragraph above, from the partition read below.** (1) The
> 0.073 is the *mean of per-cell SDs*; the pooled RMS SD over the same cells is **0.130**. They are
> two statistics of one set of cells, and the power claim does not rest on either: it rests on the
> bootstrap intervals, which are ±0.02–0.05 wide. "Could see ~0.07+" should read "could see a main
> effect of ~0.05+". (2) "Simplify" is what the **aggregate** supports. Read within the two partitions
> the registration declared, the null holds in every stratum's own interval; one of six interactions
> — checklist, bottom-vs-top difficulty tercile, +0.059 [+0.002, +0.122] — excludes zero at the
> registered 95% and not after the multiplicity correction the registration should have carried.
> The verdict is not a statement about every stratum, and it is weakest where the model is furthest
> from the ceiling. Section "Read by declared partitions" below.

## Cost — the factors are not free

USD per arm (mean per solve), and the point of the whole exercise:

| arm (A B C) | mean US$/solve | vs bare |
|---|---:|---:|
| 000 bare | 0.0502 | — |
| 001 planner | 0.0517 | +3% |
| 010 checklist | 0.0582 | +16% |
| 011 checklist+planner | 0.0514 | +2% |
| 100 repo-map | 0.0531 | +6% |
| 101 repo-map+planner | 0.0522 | +4% |
| 110 repo-map+checklist | 0.0550 | +10% |
| 111 all | 0.0563 | +12% |

Each factor adds ~3–16% to the per-solve cost and returns nothing measurable on the oracle. That is
the harness-engineering lesson made a number (`refs/harness-engineering.md`: *a good harness gets
simpler; every component encodes an assumption about what the model can't do alone, and they age*).
On `deepseek-v3.2`, these three assumptions have aged out for this task class.

## The simplify implication, and the one decision it points at

Of the three, only the **planner is on by default** in production (`--repo-map` and `--checklist` are
opt-in). The planner's effect here is **+0.003, inside the noise**, at a cost — so for this class of
coding task on this model, **`--no-plan` is the defensible default**, and turning repo-map/checklist on
is not worth it. This is a recommendation to the owner, not an auto-flip: the planner default is
global (it touches non-coding turns this venue never exercised), so the honest move is to disable it
for the measured class or A/B it in production, not to change the global default on 23 coding tasks
alone. What this bench establishes is that the *coding-shaped* scaffolding is not paying for itself.

*2026-09-15.* The one factor whose number moved under a partition is the **checklist**, and only in
the bottom difficulty tercile: +0.050 [−0.006, +0.110] over 8 tasks — an interval that spans zero on
its own. The checklist is opt-in today and stays opt-in; that stratum is the one place a registered
replication could change this paragraph, and it is priced below.

## Registered predictions, and which the run refuted

1. **Repo-map helps where there is a repo to map — REFUTED.** On the ≥10-fixture-file subset (039,
   042, 083, 085, 087, 092) repo-map Δ = **−0.012**, identical to the <10-file subset and inside the
   noise. No help even where the map has the most to say. Published as wrong.
2. **Checklist ≈ 0 on the oracle — HELD.** Δ +0.005, inside the noise.
3. **Planner — no prediction (as registered).** Δ +0.003, inside the noise.
4. **Noise floor — partly refuted.** Predicted within-cell SD ≥ 0.15; measured **0.073** — v3.2 is
   *more* consistent than the LoopsBench/Terminal-Bench floor predicted. Thresholded flip rate 0.25,
   as predicted.
5. **Cost ≥ +25% for A/B arms — REFUTED (smaller).** Measured +6–16%, not +25%. Real, but less than
   registered.
6. **Ceiling <10% of solves at 120 steps — not captured.** The chimera receipt records `usd`,
   `ending`, `attempts` but not the per-solve step count, so the ceiling fraction cannot be read from
   the artefacts. No solve hit the US$ 2 per-solve cap; the outcome numbers are unaffected.

**Self-report vs reality (secondary):** the loop's `ending == "success"` agrees with oracle ≥ 0.8 on
**331/547 = 61%** — the pilot's observation holds at scale: the loop's own verdict is not the outcome.

## Apparatus notes (the stop rules and anomalies, so the number is read honestly)

- **The first launch halted itself** on the §7 rule (4/25 rc≠0-or-receiptless): 078 and 088 need a
  public tunnel absent here (amendment 2). Excluded; the rule worked before any number was read.
- **One cell** (080 / arm-101-r2) failed once with an `AttributeError` inside the task's own oracle
  (`'list' object has no attribute 'get'`) on a specific workspace state; re-run alone it succeeded (rc=0, outcome 0.739); 080 scored on every arm. Final set is 552/552.
- **Five receiptless-but-rc0 solves** (044 ×1, 087 ×4): the agent finished (outcome captured) but no
  receipt was written — the pilot's rare concurrency anomaly. Their outcome is in; their USD is not,
  so the per-arm cost means omit 5 of 552 cells (~0.9%). Never scored 0; §2.

## What this cannot show
- **One model (`deepseek-v3.2`), one venue (23 Harness-Bench coding tasks), one host, no OS sandbox,
  no retries.** A stronger or weaker model could make the scaffolding matter; `--max-attempts 1` means
  every factor had to act inside one attempt (progress-ledger and diff-feedback were excluded as inert
  under one attempt, §3).
- **The oracle is the DV.** The harness's LLM rubric/process grade was bypassed (no proxy), so this is
  outcome-faithfulness, not prose quality.
- **A null on the outcome is not "the scaffolding never helps"** — it is "on this model and this task
  class it did not, at a measurable cost," which is exactly the case for turning it off here.

## Read by declared partitions — study 19, item A2 (2026-09-15)

Registered in [`PREREGISTRATION-partitions.md`](PREREGISTRATION-partitions.md) before any partitioned
number existed; reader `effects_by_partition.py`; the 087 correction (#468) applied first; **US$ 0**.
The question was whether the aggregate null hides strata of opposite sign, as two outside
measurements say it can (`2609.11987`: repository tasks −9.0 pp against contest tasks +23.7 pp;
`2609.13890`: +2.4 pp in the easy tercile, +21.1 pp in the hard one). Same per-task deltas as above;
within each stratum a bootstrap CI over that stratum's tasks; the interaction's CI resamples the two
strata independently (10,000 draws, seed 20260915).

### Family — the benchmark's own `task.yaml: class`: SE 16 vs other 7

| factor | SE (n=16) | other (n=7) | interaction SE − other | Bonferroni over 6 |
|---|---|---|---|---|
| A repo-map | −0.017 [−0.049, +0.014] | −0.005 [−0.031, +0.020] | −0.011 [−0.052, +0.029] | [−0.067, +0.042] |
| B checklist | +0.006 [−0.071, +0.073] | +0.004 [−0.024, +0.040] | +0.002 [−0.084, +0.074] | [−0.114, +0.097] |
| C planner | +0.016 [−0.015, +0.047] | −0.029 [−0.070, +0.002] | +0.044 [−0.001, +0.096] | [−0.014, +0.114] |

Within-cell SD: SE **0.146**, other **0.082**. Every stratum interval and every interaction spans
zero. The nearest thing to the paper's pattern is the planner — and it points the **wrong way**:
positive in the SE stratum, negative outside it, where the paper had repository tasks negative.

### Difficulty tercile — by the bare arm's mean, computed once

bottom (8): 043 0.28, 092 0.34, 042 0.51, 086 0.67, 041 0.72, 047 0.73, 085 0.75, 082 0.76 ·
middle (8): 044 0.78, 040 0.78, 064 0.83, 094 0.84, 080 0.86, 011 0.86, 022 0.88, 087 0.92 ·
top (7): 039 0.93, 045 0.95, 084 0.97, 051 0.98, 016 1.00, 083 1.00, 089 1.00.

Cross-tabulation with family, which the registration said to print because the two partitions might
be one: **bottom 7 SE / 1 other · middle 4 / 4 · top 5 / 2.** They are not independent — the hard
tercile is nearly all SE — so the two reads below are two views of largely the same tasks.

| factor | bottom (n=8) | middle (n=8) | top (n=7) | interaction bottom − top | Bonferroni over 6 |
|---|---|---|---|---|---|
| A repo-map | −0.004 [−0.039, +0.037] | −0.034 [−0.087, +0.012] | +0.001 [−0.005, +0.006] | −0.005 [−0.040, +0.038] | [−0.047, +0.053] |
| **B checklist** | **+0.050 [−0.006, +0.110]** | −0.025 [−0.169, +0.086] | −0.010 [−0.027, +0.004] | **+0.059 [+0.002, +0.122]** | **[−0.013, +0.144]** |
| C planner | +0.014 [−0.041, +0.070] | −0.006 [−0.054, +0.036] | −0.001 [−0.021, +0.016] | +0.015 [−0.041, +0.074] | [−0.062, +0.094] |

Within-cell SD: bottom **0.154**, middle **0.154**, top **0.037**; all 23 tasks by the same statistic
**0.130**.

### What the registered rule says — and the defect in the registration

- **Family partition, all three factors; tercile partition, A and C:** every stratum CI spans zero
  and the interaction spans zero → decision-table **row 1**. Null in the aggregate and in each
  declared stratum; not powered to see a ±0.10 interaction.
- **Checklist on the tercile partition:** both stratum intervals span zero, and the interaction
  **+0.059 [+0.002, +0.122] excludes zero by 0.002** → **row 3 fires by the letter**: "the aggregate
  null is withdrawn as a summary and *simplify* comes out of the recommendations."
- **The registration is defective, and the defect is ours.** It reads *six* interactions (three
  factors × two partitions) and says nothing about correcting for six looks. `chimera/eval/anytime.py`
  and study 10 already settled this project on Bonferroni across decisions; the same sentence
  belonged here and was not written. Corrected over six, the interval is **[−0.013, +0.144]**.
- **Deviation, stated:** the correction was added *after* the numbers were seen. A rule changed after
  the fact is not allowed to decide anything here, whichever way it cuts — so it does not overturn
  row 3, and row 3 does not stand uncorrected either. Both intervals are on the record. The honest
  sentence is: **the checklist's tercile interaction fires by the letter of the registration and does
  not survive the correction the registration should have carried.** Neither confirmed nor withdrawn.
  It is the one number in this bench that a registered replication would settle.

### Three things that make the firing weaker than its line in the table

1. **The top tercile cannot exhibit an effect, by arithmetic.** Its bare means are 0.93–1.00; a
   factor cannot lift a task above 1.00, so every effect there is bounded by the headroom (0.00–0.07),
   and its SD is 0.037 because there is nothing left to vary. The "interaction" is the bottom
   stratum's +0.050 — which spans zero on its own — read against a stratum that is a fixed point.
   Part of the DATS pattern (+2.4 easy / +21.1 hard) is this same arithmetic: where the model already
   passes, scaffolding cannot help, by construction rather than by finding. **The stratum with the
   power is the one where scaffolding cannot help; the stratum where it could help has a noise SD of
   0.154, twice the aggregate.**
2. **The terciles do not order.** If the effect were the paper's — growing with difficulty — the
   middle should sit between bottom and top. It sits *below* the top: +0.050 / −0.025 / −0.010. The
   hypothesis's own signature (§2s: measure the signature beside the aggregate) is absent.
3. **The lower bound is +0.002** on a bootstrap with 10,000 draws over 8 and 7 tasks. A different seed
   would move it across zero; that is what "fires by the letter" means and no more.

### Predictions

- **P1 — held.** Stratum intervals are wider than the aggregate's (the SE checklist interval is
  ±0.07 against ±0.05), and 20 of the 21 stratum and interaction intervals span zero.
- **P2 — wrong in sign.** The planner was the factor predicted to carry the paper's pattern,
  negative in SE and positive outside. Measured the reverse: SE +0.016, other −0.029, interaction
  +0.044 [−0.001, +0.096] (Bonferroni [−0.014, +0.114]), spanning zero on both readings. The
  prediction failed in direction, and the interval does not license the opposite claim either.

### Noise floor by stratum, and the statistic behind the published 0.073

The verdict's noise floor is the **mean of per-cell SDs** (`read_results.py`, `statistics.mean` of
`pstdev` per cell); the strata above are compared with the **pooled RMS** (root of the mean per-cell
variance), which is what a difference of means inherits. Over the same 23 tasks the two give 0.073
and 0.130. Neither number carried the power claim — the bootstrap intervals did — but the sentence
"the SD is 0.073, so the design could see ~0.07+" conflated the two, and is amended above. By family,
SE 0.146 against other 0.082: the benchmark's own class is also its noise class.

### What ships, what does not, and the price of settling it

- **Ships:** the reader, the registration, and this section. No harness file was changed; no default
  flips. The verdict now says which partitions were looked at and what each had the power to see —
  which every row of the decision table required, and which the word "simplify" lacked.
- **Does not ship:** any claim that the checklist helps on hard tasks. +0.050 [−0.006, +0.110] over
  8 tasks is an interval that includes zero, read on a partition confounded with family, with the
  hypothesis's monotone signature absent.
- **To settle it:** the bottom-tercile checklist main effect, on its own, with a registered threshold
  and the correction written in. Re-running arms 000 and 010 on the 8 bottom tasks at k=6 is 96
  solves ≈ **US$ 5** at the factorial's per-solve cost (all eight arms at k=3 ≈ US$ 10). The design
  effect measured in `bench/design_effect` (ICC 0.706) says replicas of the *same* task buy little;
  a replication that wants power should spend on more hard tasks, not more replicas — and there are
  only 8 of them in this venue. That is the real limit, and it is the venue's, not the analysis's.
