# Pre-registration — re-reading the published intervals under PROTOCOL §11

**2026-10-05**, study 30 item S30-34. Written and committed before `reread.py` exists and before any
re-computed interval has been seen. US$ 0: no model is called; every number comes from data already
in this repository (plus, for one partition, the benchmark's own `task.yaml: class` field, copied
into the reader and cited).

## Why

`bench/PROTOCOL.md` §11 (same commit series) retires three methods that printed published intervals:

1. **the percentile bootstrap at N < 100** (arXiv 2609.35815: 88% coverage at nominal 95%);
2. **the conditional paired interval** `chimera/eval/paired.py` printed until this series — Wilson on
   the discordant pairs scaled by the observed `m/n` — measured here at 41–88% coverage of a real
   difference (`tests/test_the_paired_interval_covers_the_difference.py`);
3. **Newcombe's paired interval without his continuity correction to phi**, the copy three readers
   carried (`tests/test_stats_helpers_have_one_home.py`).

A verdict printed by a retired method has not been shown to be wrong. It has been shown to rest on an
instrument that undercovers. This file fixes, before the numbers, what is re-read, how, and what
counts as a correction.

## What is re-read

**A. Percentile bootstrap at small N** (the six readers study 30 named):

| bench | published interval | re-read with |
|---|---|---|
| `harness_bench` main effects (23 tasks) | bootstrap over tasks | `mean_t_interval` on the per-task deltas |
| `harness_bench` partitions, each stratum (7–16 tasks) | bootstrap over the stratum | `mean_t_interval` |
| `harness_bench` partitions, interaction between strata | two-sample bootstrap | `welch_t_interval`, at 95% and at Bonferroni 95/6 |
| `chat_history` primary REAL − FLAT (32 tasks) | cluster bootstrap over tasks | `mean_t_interval` on per-task differences |
| `review_judge` C − A in J (814 out of sample; 105 in sample) | paired bootstrap within label | MOVER sum of two `bonett_price_paired` intervals (ΔTPR on the bad items, ΔFPR on the good, disjoint item sets) |
| `edit_tools` median Δ edit calls and Δ tokens (22 pairs) | bootstrap of the median | `median_interval` (binomial order statistics) |
| `wake_gate` AUROC (34 scored) | bootstrap over items | `auroc_hanley_mcneil` |
| `stop_gate` ΔAUROC(A3 − A1) (118 turns) | paired bootstrap over turns | read only: the three arms' scores are identical, so Δ = 0 under any method |

**B. The conditional paired interval.** Every committed JSON holding a `PairedResult.summary()` —
found mechanically by a `discordant` field holding the two counts (10 files, 22 summaries) — re-read with
`bonett_price_paired` on the same counts, plus `review_judge`'s ΔTPR and ΔFPR, which its readers
printed with the same method.

**C. Newcombe without the phi correction.** `sharded_recap`'s pilot gate (F − A), `chat_history`'s
paired line (REAL − FLAT over 128 pairs) and `spoken_standard`'s paired table (speakable,
format-speakable), re-read with `newcombe_paired` — and, for the record, with `bonett_price_paired`.

## Reproduction first (§2aa)

Before printing a new interval for any verdict, `reread.py` recomputes the **published** interval
with the **published** method (same seed, same draws where a bootstrap) from the same data, and
compares it to the published figure at the published precision. A verdict that does not reproduce is
reported as **not reproduced**, its new interval is not read, and the mismatch is a finding in its own
right.

## The correction rule (fixed now)

A published verdict gets a **dated correction in its own RESULTS file** when, under the new interval:

- its decisive bound moves to the other side of the criterion the verdict was read against — zero,
  or the margin its registration declared; or
- the decision-table row its registration names changes.

Any other movement is listed in this bench's RESULTS only. A correction states the old interval, the
new one, the method, and which sentence of the old verdict it withdraws. Where the old verdict was
already retracted for another reason, the re-read says so and adds nothing.

## Predictions (written before any number)

- **P1.** `harness_bench`, checklist × tercile interaction (published +0.059 [+0.002, +0.122]): the
  Welch interval's lower bound falls **below zero**, so "row 3 fires by the letter" no longer holds.
  A correction follows.
- **P2.** `harness_bench` main effects and the other five interactions: all still span zero.
- **P3.** `chat_history`: non-inferiority still not shown (t lower bound below −10 pp). The verdict
  stands; no correction.
- **P4.** `review_judge`, C − A in J out of sample: still excludes zero. ΔTPR and ΔFPR widen, mostly
  on the side the conditional interval pinned to the point estimate.
- **P5.** In list B at least one summary that printed `significant: true` reads not significant.
- **P6.** `edit_tools`: the edit-calls interval still excludes zero; the criterion stays met.
- **P7.** `wake_gate`, `stop_gate`: no verdict rests on the AUROC interval; no correction.
- **P8.** `spoken_standard` speakable B − A (published [−19.7, +0.6]): the corrected interval is
  wider and still spans zero; format-speakable still excludes it. No correction.

## §11 — the interval

As in the table above; every function is from `chimera/eval/proportions.py`. Bootstraps appear only
in the reproduction step, never as a re-read.

## §12 — equivalence

This re-read makes no equivalence claim. Where a published verdict reads a null as "the factor does
nothing" without a declared margin (`harness_bench`), the re-read prints, as a description, the
smallest symmetric margin the 90% t interval fits inside — the margin a TOST would have needed — and
does not turn it into a verdict.

## §13 — controls

The trivial-agent, random-arm, hijack, argument-predicate, detection, placebo and rule-withdrawn
controls are about scoring a model; nothing is scored here. The control that applies is the
reproduction step above: each reader must first compute what was published.

## §14 — model scope

Inherited unchanged from each re-read bench; this re-read changes no model claim and recommends no
removal.
