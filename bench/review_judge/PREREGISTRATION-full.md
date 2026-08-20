# Pre-registration — the confirmatory run over the full Diff Level slice

**Written and committed before the run.** Fifth and last document here. The pilot promised this:

> This pilot is **not** the confirmatory run. It fixes the operating point and measures the cost per
> item; a full-slice run (n=1017) will be pre-registered separately, citing this document.

## Why more data now, when five arms already said what they said

Two reasons, and only the second is new.

**Precision.** At n=105 the sample holds 53 incorrect comments, and a 95% interval on a proportion
that size is ±12 points. Every difference this directory has reported is roughly the width of its own
error bar. The full slice holds **263 incorrect and 754 correct** comments — intervals shrink by about
half, and differences that were suggestive become statable.

**And the one that matters: arm C has never been tested out of sample.** Its rubric was written after
reading the 41 comments arms A and B both missed — from *these* 105 items. Measuring it on the same
105 is fitting, and its 60.4% recall and +21.9 J are in-sample figures that this directory has been
quoting without that label. The remaining **912 rows were never looked at by anyone designing a
prompt**, and they are the first honest test of whether the rubric change generalises.

## The arms

Both over the full 1017-row Diff Level slice, same judge, same window, temperature 0:

- **A (cautious)** — the original rubric. The number that describes the judge as it stands.
- **C (split)** — both grounds, the counterargument before the verdict.

Two arms because one would answer nothing new: A alone re-measures 15.1% with tighter bounds, and C
alone cannot be compared to anything at this sample size. Paired over the same items.

## The split that must be reported, and reported separately

Every result below is reported **three times**: over all 1017, over the **105 in-sample** items, and
over the **912 out-of-sample** items.

**The out-of-sample figure is the headline.** If C's advantage over A shrinks materially between the
two, the rubric was fitted to the pilot's misses and the honest reading of the last five arms changes
— retroactively, and this document says so before the number exists.

## Primary comparisons, fixed here

1. **A's rejection recall out of sample**, with its Wilson interval. This is *the* number for "how
   well does Chimera's judge catch a plausible-but-wrong finding".
2. **C − A in Youden's J, out of sample**, paired. J separates real discrimination from a threshold
   move; the pilot's +6.8 (15.1 → 21.9) is the effect being replicated.
3. **C's false rejection out of sample**, against the standing 20% ceiling. The pilot's 38.5% is
   expected to hold; a materially lower number out of sample would itself be evidence of fitting, in
   the opposite direction.

## Predictions, before the run

| | in sample (known) | out of sample (predicted) |
|---|---|---|
| A recall | 15.1% | 12–20% |
| C recall | 60.4% | 45–60% |
| C false rejection | 38.5% | 32–45% |
| C − A in J | +6.8 pp | **+2 to +7 pp** |

The J prediction is deliberately lower than the in-sample value: a rubric written against known
errors should lose some of its edge on unseen ones, and how much is exactly what this run measures.
If it holds at +6.8 or better, the improvement is real and general; if it collapses to near zero, arm
C was a description of 41 specific comments.

## Uninformative conditions, unchanged

Unparseable answers > 10%; a rejection rate at either extreme; rows whose diff cannot be fetched are
dropped and counted, never re-drawn. Added for this run: **if fewer than 700 of the 1017 rows survive
diff fetching**, the out-of-sample half is too thin to carry the headline and the run is reported as
a precision improvement on the pilot rather than as a replication.

## Cost, and the change that makes it feasible

~US$ 11 for both arms, and about 16 hours each if run the way the pilot ran — one call at a time. The
runner gains bounded concurrency for this, which changes no measurement: each item is an independent
call, results are written in the sample's fixed order, and the seed, sample and prompts are untouched.
Six in flight, two arms in parallel, ~3 hours.

This is the last planned run in this directory. Whatever it says, the answer to "how good is the
judge" stops being provisional.
