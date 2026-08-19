# Pre-registration — is the judge's caution the judge, or the prompt telling it to be cautious?

**Written and committed before the second arm ran.** Extends `PREREGISTRATION.md`, which fixed the
sample and the metric, and whose pilot produced 15.1% rejection recall at 0% false rejection.

## The question

The pilot's system prompt states an asymmetry, taken from open-code-review's filter:

> The two mistakes are not equally bad. Keeping a wrong comment costs a reader a few seconds.
> Rejecting a correct one destroys a real finding and nobody sees it again. **When your evidence
> falls short, APPROVE.**

The judge then approved 92.4% of everything. A model told to approve under uncertainty, which
proceeds to approve under uncertainty, has not been shown to lack judgement — it has been shown to
follow instructions. `RESULTS.md` says exactly that, and this experiment is the way to separate the
two rather than leave it as a caveat.

**This is the highest-risk experiment in this directory**, and the risk is not technical: running
prompt variants until one scores well is fitting, not measuring. So the arms, the comparison and the
decision rule are fixed here, the second arm runs once, and whatever it says is what gets published.

## The two arms

Identical in every respect except three sentences. Same 105 items, same seed, same model
(`CHIMERA_FUSION_JUDGE`), same temperature (0.0), same parser, same diffs from the same cache.

**A — cautious** (already run, `results/cautious/`): the prompt above.

**B — neutral**: the asymmetry paragraph is replaced by one neutral sentence — *"Judge the comment on
its merits."* Nothing else changes.

What is deliberately **not** changed in B: the definition of a valid rejection ("the code the comment
describes is not in this diff, or a line of the diff contradicts its central claim"). Removing that
too would move two variables at once, and the result would name neither.

## Comparison

**Paired, not two independent samples.** The same item is judged by both arms, so the analysis is
McNemar over the discordant pairs, via `chimera/eval/paired.py` — the same code the other suites use.
Comparing two independent proportions here would throw away the pairing and widen the interval for
nothing.

**Primary:** the difference in rejection recall (B − A) on the 53 incorrect comments, with the paired
confidence interval.

**Constraint, unchanged from the pilot:** false rejection ≤ 20%. An arm that finds more bad comments
by rejecting good ones has not improved; it has moved along a trade-off that was already available.

## Decision rule, fixed in advance

| outcome | reading |
|---|---:|
| B's recall exceeds A's, interval excludes zero, and B's false rejection ≤ 20% | the pilot measured the PROMPT. 15.1% is not the judge's ceiling, and the cautious wording is a product decision rather than a finding. |
| B's recall does not exceed A's (interval includes zero) | the pilot measured the JUDGE. The instruction was not what held it back, and 15.1% stands as the number. |
| B's recall exceeds A's but false rejection > 20% | a trade-off, not a gain. Report both numbers and neither as an improvement. |

**Both arms are published either way**, including the case where the neutral arm is worse — which is
a real possibility worth naming: an instruction to weigh evidence carefully can raise a model's
willingness to reject *correct* findings, and that would show up as recall barely moving while false
rejection climbs.

## What would make this uninformative

The same four conditions as the pilot, applied to arm B: unparseable answers > 10%, a rejection rate
at either extreme, and any item whose diff could not be fetched (dropped and counted, never
re-drawn). Plus one specific to a paired design: **fewer than 10 discordant pairs**, in which case
McNemar has nothing to work with and the honest answer is "this comparison cannot resolve it at
n=105".

## Cost

~US$ 0.40, the same as the pilot. One run.
