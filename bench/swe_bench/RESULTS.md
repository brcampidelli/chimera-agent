# SWE-bench Verified — the Chimera scaffold on real django bugs

Three pre-registered runs, all graded **only** by the official `swebench` 4.1.0 harness in Docker —
never self-reported. Design, slice, arms and predictions were fixed in
[`PREREGISTRATION.md`](PREREGISTRATION.md) **before any model call** of each run.

| run | slice | baseline | + Chimera | paired Δ | 95% CI | |
|---|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 19 | 36.8% (7/19) | 36.8% (7/19) | **+0.0%** | [−8.5%, +8.5%] | not significant |
| 2 (`max_steps=30`) | same 19 | 42.1% (8/19) | 57.9% (11/19) | **+15.8%** | [−1.9%, +15.8%] | not significant |
| **3 (replication)** | **41 unseen** | 34.1% (14/41) | 43.9% (18/41) | **+9.8%** | [−3.5%, +16.7%] | not significant |
| **pooled (secondary)** | **60** | 36.7% (22/60) | 48.3% (29/60) | **+11.7%** | **[+0.8%, +16.4%]** | **significant** |

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
3. **Which component did it.** Amendment 3 cut the middle arm (plain scaffold, no diff-gate) for cost,
   so the result reads "**scaffold plus gate** beats the bare model" — never "the gate did it". Run 2's
   instance-level evidence argued the gate contributed little; a clean attribution needs the arm we
   dropped.
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
