# Pre-registration — arm C: the rubric the reading blamed

**Written and committed before arm C ran.** Third document in this directory; the first fixed the
sample and metric, the second fixed the two-arm comparison, this one fixes the rubric change that
the reading of the 41 misses pointed at.

## Where this comes from

Arms A (cautious) and B (neutral) caught 8 and 10 of 53 incorrect comments, and **41 survived both**.
Three readers went through those 41 with the diff and the judge's own reason in hand
(`RESULTS.md`, "Reading the 41"). Their finding was not about the model:

> Reject only when you can point at the reason: the code the comment describes is not in this diff,
> or a line of the diff contradicts its central claim.

That footer — ours, identical in both arms — has **no ground for "true, but not a defect"**, which is
12 of the 41. Praise is grounded in the diff and nothing contradicts it, so approving was obligatory.
For suggestions it is worse: the absence of the suggested thing is the comment's premise, so the
rejection condition cannot fire at all.

Arm C changes that footer. It is the first arm whose hypothesis comes from evidence rather than from
a guess, and it is also the arm most at risk of being fitted to the sample it was designed on — which
is why the prediction below is written as a number, before the run.

## The three changes, exactly

1. **Two grounds added to the rubric**, so the judge may reject a comment whose premise is true:
   - the comment asserts no defect at all — praise, a paraphrase of the diff, or a preference;
   - what it reports is pre-existing rather than introduced by this diff.
2. **A required `strongest_counterargument` field, serialised BEFORE the verdict.** In at least six
   of the 82 justifications the judge stated the disconfirming fact and approved anyway; the aim is
   not to make it notice, it is to make that sentence bind. Field order is load-bearing, which the
   open-code-review authors measured: with the decision first, the model commits and then reasons.
3. **The full reason is recorded.** `ask()` truncated at 200 characters and all three readers said
   they had judged a first sentence rather than a chain of reasoning. Costs nothing and makes the
   next audit possible.

Everything else is identical to arms A and B: same 105 items, same seed, same judge, same
temperature, same diffs from the same cache, same parser.

## Prediction, stated before the run

The reading claimed changes 1 and 2 reach the 12 "not a defect" items and some of the 15 reasoning
failures. Taking that at face value and being explicit about it:

**Predicted rejection recall: 40–55%** (21–29 of 53), against 15.1% for arm A.

If the result lands far below that band, the reading's diagnosis was wrong and this document is the
evidence that the prediction was made in advance rather than fitted afterwards.

## The declared ceiling

Of the 53 incorrect comments, the reading found **12 that no change on our side can convert**: 6
unfalsifiable ("consider adding error handling"), 3 needing API semantics no context supplies, 3
whose labels a careful reader contests. **The realistic maximum is therefore 41/53 = 77%**, and any
report of arm C that quotes a recall without quoting this ceiling is misleading by omission.

## The risk this change carries, and it is the main one

**81% of the CORRECT comments in this slice are also phrased as suggestions.** A judge newly licensed
to reject "preferences" has been handed a rule that fits four fifths of the good comments too. If
arm C buys recall by rejecting correct findings, that is not an improvement — it is a move along a
trade-off that was always available, and the pilot could have had it by lowering its bar.

Hence the constraint below is unchanged and binding.

## Decision rule, fixed in advance

| outcome | reading |
|---|---|
| recall ≥ 40% **and** false rejection ≤ 20% | the rubric was the bottleneck. The reading is confirmed and the judge's own ceiling is higher than 15.1% |
| recall 20–40% and false rejection ≤ 20% | the rubric was part of it. Report the partial gain, no product claim |
| recall < 20% (paired CI includes arm A) | the rubric was not the bottleneck. The reading's diagnosis is refuted and 15.1% stands as a fact about the judge |
| false rejection > 20%, at any recall | a trade-off, not a gain. Both numbers reported, neither as an improvement |

Comparison is **paired against arm A** over the same items (`chimera/eval/paired.py`), with the same
uninformative conditions as before — including fewer than 10 discordant pairs, which is what stopped
arm B from resolving anything.

## Cost

~US$ 0.45. The counterargument field adds output tokens; the untruncated reason costs nothing extra
at the provider, only on disk. One run.
