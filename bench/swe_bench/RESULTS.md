# SWE-bench Verified — the Chimera scaffold on real django bugs

Four pre-registered runs, all graded **only** by the official `swebench` 4.1.0 harness in Docker —
never self-reported. Design, slice, arms and predictions were fixed in
[`PREREGISTRATION.md`](PREREGISTRATION.md) **before any model call** of each run.

| run | slice | baseline | + Chimera | paired Δ | 95% CI | |
|---|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 19 | 36.8% (7/19) | 36.8% (7/19) | **+0.0%** | [−8.5%, +8.5%] | not significant |
| 2 (`max_steps=30`) | same 19 | 42.1% (8/19) | 57.9% (11/19) | **+15.8%** | [−1.9%, +15.8%] | not significant |
| **3 (replication)** | **41 unseen** | 34.1% (14/41) | 43.9% (18/41) | **+9.8%** | [−3.5%, +16.7%] | not significant |
| **pooled (secondary)** | **60** | 36.7% (22/60) | 48.3% (29/60) | **+11.7%** | **[+0.8%, +16.4%]** | **significant** |
| 4 (attribution) | run 3's 41 | 34.1% | *scaffold only* 39.0% | **+4.9%** | [−7.6%, +14.2%] | not significant |

**Bottom line.** Run 2's +15.8% was a 3–0 sweep on three informative pairs — exactly the shape a lucky
sample produces, and the pre-registration gave it a **one-in-three chance of being just that**. Run 3
tested it on **41 instances whose outcomes we had never seen**, changing nothing else. The effect
**reappeared**: +9.8%, inside the registered +5-to-+20 band, on a slice that turned out *harder* than
run 2's (baseline 34.1% vs 42.1%). Neither out-of-sample run is individually significant; the pooled
n=60 is, and it was pre-registered as **secondary** precisely because it mixes seen with unseen data.

---

## Run 3: the out-of-sample replication

```
n=41 django "<15 min fix" instances, NONE used by run 2 | deepseek-chat-v3.1 | pass@1
  baseline      34.1%  (14/41)   --no-plan --no-manager --max-attempts 1 --max-steps 30
  chimera+gate  43.9%  (18/41)   --repo-map --progress-ledger --replan --checklist
                                 --max-attempts 3 --require-diff --max-steps 30
  paired Δ +9.8%   95% CI [-3.5%, +16.7%]   -> not significant
  discordant: chimera-only 6 / baseline-only 2
  concordant: both resolved 12 | both failed 21
```

**Nothing about the configuration changed** — same arms, same model, same step budget, same timeout.
That is what makes this a replication rather than another exploration. The 41 instances were frozen by
a deterministic rule (id order, never used by run 2) and **gold-validated 41/41** before any model call.

### The mechanism replicated too, and it is the interesting part

| | patches | resolved | **precision when it edited** |
|---|---|---|---|
| run 2 baseline | 14/19 | 8 | 57% |
| run 2 chimera | 16/19 | 11 | **69%** |
| **run 3 baseline** | **28/41** | **14** | **50%** |
| **run 3 chimera** | **27/41** | **18** | **67%** |

In run 3 the scaffolded arm produced **fewer patches** (27 vs 28) and resolved **more** (18 vs 14). It
does not win by acting more; it wins by acting **better** — the same signature run 2 showed, now on
unseen data. Twenty-two live partial readings during the run all showed the two arms editing at
near-identical rates; the entire difference arrived at grading.

### Why the 9–2 matters more than either Δ

Across both out-of-sample-capable runs the discordant pairs — the only ones a paired test reads —
are **9 for Chimera against 2 for the baseline**. Under the null, a 9–2 split has probability ≈ 2.6%.
Run 2's 3–0 was suggestive (1/8); two disjoint slices agreeing in direction and magnitude is a much
harder thing to get by chance than one lucky sweep.

## Run 4: the middle arm — and a prediction of ours that was wrong

Run 3's write-up named its own limitation: cutting the plain-scaffold arm for cost meant the result
read *"scaffold **plus** gate beats the bare model"*, never *"the gate did it"*. Run 4 restored that arm
on **the same frozen 41-instance slice**, so all three arms differ by exactly one component.

```
baseline          34.1%  (14/41)
scaffold          39.0%  (16/41)      + --repo-map --progress-ledger --replan --checklist
scaffold + gate   43.9%  (18/41)      + --require-diff

  scaffold      vs baseline :  +4.9%  95% CI [-7.6%, +14.2%]  ns   discordant +5/-3
  scaffold+gate vs baseline :  +9.8%  95% CI [-3.5%, +16.7%]  ns   discordant +6/-2
  gate          vs scaffold :  +4.9%  95% CI [-7.6%, +14.2%]  ns   discordant +5/-3
```

**Both components contribute, in roughly equal halves.** No single comparison is significant.

### The prediction we registered was wrong, and so was a run-2 reading

Amendment 5 predicted *"scaffold vs baseline: +5 to +15 pp, i.e. **most of** run 3's +9.8%"* and
*"the gate adds little, −5 to +5 pp"*. The reasoning was run 2's instance-level evidence, where the
gate converted only 1 of 4 genuine non-edits.

**The scaffold delivered half, not most; the gate delivered the other half** — at the top of the band
we allowed it. The pre-committed reading that applies is the third one: *"both above baseline — report
the decomposition."*

This also **withdraws a claim made in run 2's write-up**, that `--require-diff` "is not what produced
the gain". Judged only on the four instances where the baseline failed to edit, that looked right.
With a proper control arm, the gate's contribution is comparable to the scaffold's. The earlier
reading was made from too narrow a slice of the evidence.

### The one clean pattern: precision climbs with each component

| arm | patches | resolved | **precision when it edited** |
|---|---|---|---|
| baseline | 28/41 | 14 | **50%** |
| scaffold | 27/41 | 16 | **59%** |
| scaffold + gate | 27/41 | 18 | **67%** |

All three arms edit at essentially the same rate (27–28 of 41). What changes is how often the edit is
*right* — a monotone ladder, and the same mechanism run 2 and run 3 both showed. Twenty live partial
readings during run 4 tracked patch counts that ended in a three-way tie; the entire signal was in
grading, again.

### What this decomposition cannot support

**The additivity is too tidy to trust at this resolution.** 4.9 + 4.9 = 9.8 is elegant, but each
comparison rests on 5–6 discordant pairs at n=41. Treat it as "both contribute, order-of-magnitude
similar", not as a measured 50/50 split.

**Between-run drift is an accepted confound, stated in Amendment 5 before running.** The baseline and
scaffold+gate arms are run 3's, executed a day earlier; the scaffold arm ran the next day. Instance
pairing is exact, but model non-determinism between runs is something this design cannot separate.

**Run 4 validity:** 0 timeouts (vs 2 and 1 in run 3's arms), 0 infrastructure errors, 14 empty patches
(comparable to 13 and 14). Cost US$ 12.96, 6.7 h wall time — **52% over our US$ 8.53 estimate**, because
the estimate used run 3's *blended* per-solve cost instead of the comparable expensive arm's. That
mistake exhausted the budget: **US$ 0.00 remains**, so the other open paths (a second repository, and
Q2's absolute score on the full 500 at ~US$ 104) are now blocked on funding, not on design.

## Validity (pre-registration §7)

| gate | run 3 |
|---|---|
| timeout symmetry | **2 vs 1** — symmetric; the scaffolded arm did not collapse despite 3 attempts/solve |
| infrastructure errors | **0 / 0** |
| gold validation | **41/41** before any spend |
| slice overlap with run 2 | **0** |
| comparability of the fresh slice | patch rate 68% vs run 2's 74% — comparable, though resolution shows it is genuinely harder |

Timeouts count as failures **against the arm that had them**, so the 2–1 split runs against the
scaffolded arm, not for it. Empty patches are reported separately from timeouts: baseline 13 empty
(2 of them timeouts), chimera 14 empty (1 timeout).

**Cost:** US$ 17.05 for 82 solves. **Wall time:** 3.6 h (baseline) + 8.7 h (scaffold) = 12.3 h. The
scaffolded arm costs ~2.4× the baseline's time for its four extra resolutions.

## What this establishes — and the four things it does not

**Establishes.** On real django bug-fixes, with a competent model and an adequate step budget, the
Chimera scaffold resolves **more instances than the bare model, and the effect replicates
out-of-sample**. The mechanism is identified and stable: **higher precision per edit**, not more
edits. That is the project's strongest external evidence, and it survived the replication test that
was designed to kill it.

**Does not establish, and must not be claimed:**

1. **A SWE-bench Verified score.** 43.9% / 48.3% are on a **deliberately easy, single-repo slice**
   chosen so a paired A/B has room to measure. A Verified score needs the full 500 and remains
   deferred. This number must never be presented as one.
2. **A significant out-of-sample result.** Run 3's primary is **not significant** (CI crosses zero).
   The significant number is the **pooled secondary**, which mixes seen with unseen data — it buys
   power, and the out-of-sample estimate is the one that carries evidential weight. Reporting the
   pooled alone would be picking the flattering statistic after the fact.
3. ~~**Which component did it.**~~ **Closed by run 4** (above): the middle arm was restored and both
   components contribute in roughly equal halves — scaffold +4.9%, gate a further +4.9%. What remains
   unsupported is a *precise* split: each comparison rests on 5–6 discordant pairs, so "both
   contribute, similar order of magnitude" is the claim, not "50/50".
4. **Generalisation beyond django, this model, or this difficulty stratum.** One repository, one
   model, one stratum, one run per arm.

## Run 1 and run 2 (kept as published)

**Run 1** was an **exact zero** and is published unchanged. It measured a starved agent: 8 tool-calling
steps against a 250 MB repository, with the scaffold's strongest mechanism disabled. Both faults were
**ours**, recorded as such.

**Run 2** fixed them and returned +15.8% on 3–0. It also shipped a **retraction**: the mechanism we had
traced for run 1's empty patches was wrong — the cure was the step budget, not the diff-gate we blamed.
The empty rate fell just as far in the *baseline*, which has neither gate nor scaffold.

## Methodological notes

- **The replication was designed to be able to fail**, and the failure branch was written down first:
  "*if the fresh slice comes back at ~0%, run 2 was a lucky sweep — retract it with the prominence it
  was published with*." It did not come back at ~0%, and that branch is why this result means something.
- **Registered predictions were numeric and were met**: +5 to +20 pp (got +9.8%), significance
  "genuinely uncertain" at n=41 (it was not significant), empty-patch rate comparable to run 2 (it was).
- **A one-in-three chance of null-or-negative was registered before the run**, not claimed afterwards.
- **Gold validation caught a broken instance before any spend** in run 2 (`django-10097`, whose own
  *reference* patch fails grading) and it stays excluded — including from run 3's candidate pool, where
  building the fresh list from the post-drop slice had silently re-admitted it.

## What would settle the remaining questions

- **A Verified score (Q2):** the full 500, a strong model, reported as an absolute — a separate,
  much more expensive run.
- **Attribution:** restore the middle arm (plain scaffold at 30 steps) to separate scaffold from gate.
- **Beyond django:** the same design on a second repository would test whether this is a django effect
  or a scaffold effect.
