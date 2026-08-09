---
name: publish-the-run-that-did-not-work
description: Report the experiment that failed, preserve the superseded one unchanged, and never re-roll for significance. Selection is the bias, not the number.
version: 0.1.0
kind: pattern
triggers:
- the A/B came out negative
- rerunning the benchmark
- the result contradicts what we claimed
- writing up a measurement
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You ran a comparison to decide whether a change helps, and the result is inconvenient: negative,
not significant, or contradicting something already announced.

It does not apply to a run that was simply broken — a crashed harness measured nothing, and
publishing it as a result would be its own kind of noise. Fix and rerun, and say that you did.

## Do

1. Write down, before running, what outcome would kill the claim. If you cannot name it, the
   experiment cannot fail, and an experiment that cannot fail is not one.
2. Report the effect with an interval, not a point estimate. `+9.8pp [−3.5, +16.7]` says something
   `+9.8pp` does not.
3. When a later run supersedes an earlier one, keep the earlier one published and unedited, and
   link the correction. Amending it removes the only evidence that the correction happened.
4. If a claim does not survive replication, retract it in the same place it was made, in the same
   size type.

## Avoid

Rerunning until it looks better and reporting the best run. That is not a bad measurement, it is a
selected one, and selection cannot be detected in the number afterwards by anyone — including you.

Avoid quietly dropping the negative result while keeping the positive one from the same series.
Avoid pooling a fresh sample with an old one *after* seeing that pooling helps; if pooling was not
pre-registered as primary, it is secondary and must be labelled that way.

And avoid describing a non-significant delta with words that imply significance: "improved",
"lifted", "up from". "Not significant on its own" is the sentence.

## Check

Ask what a sceptical reader could conclude from the artefacts you published. If the answer requires
trusting that you did not discard anything, the artefacts are incomplete — publish the journal, the
per-item outcomes, or the raw pairs.

Then check the claim in your own README against the results section. If one says a thing the other
retracts, the retraction is the true one and the claim has to move.

## Risk

This is slower, and it publishes work that looks like failure to anyone reading a single page out of
context. Some of those readers will be evaluating whether to use the thing.

The subtler cost: a project that publishes negatives can be quoted against itself. That is a real
price, and it is smaller than the alternative — one discovered overstatement makes every other
number you have published worthless.
