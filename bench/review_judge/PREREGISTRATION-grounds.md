# Pre-registration — arms D and E: which of arm C's two grounds carries the cost?

**Written and committed before either arm ran.** Fourth document here. The first fixed the sample and
metric, the second the two-arm comparison, the third the rubric change, and this one splits that
change in half because it moved two things at once.

## What arm C left unanswered

Arm C added **two** rejection grounds and produced 60.4% recall at 38.5% false rejection — the axis is
real and the operating point is unusable. But with both grounds moving together, nothing says which
one bought the 24 extra catches and which one destroyed the 20 correct findings.

The two are not alike:

- **"asserts no defect"** — praise, paraphrase, a preference about naming or style. Judging this
  means deciding whether a suggestion is substantive, and *a suggestion about naming and a suggestion
  about a real bug have identical form*. 81% of the correct comments in this slice are phrased as
  suggestions.
- **"pre-existing rather than introduced by this diff"** — objective. A diff shows what it changed;
  either the reported thing is in the added lines or it is not.

If those two behave differently, the second is adoptable on its own and the first needs a boundary
this experiment cannot draw. If they behave the same, the distinction is not where the cost lives and
the next hypothesis has to come from somewhere else.

## The arms

Identical to arm C in every other respect — the two questions, the required
`strongest_counterargument` before the verdict, the cautious stance, same 105 items, same seed, same
judge, temperature 0.

- **D — "not a defect" only.** Arm C's rubric with the *pre-existing* ground removed. Keeps the two
  sentences that belong to this ground: *"A true statement is not a finding. A suggestion to add
  something is not evidence that its absence is a defect."*
- **E — "pre-existing" only.** Arm C's rubric with the *asserts no defect* ground and those two
  sentences removed.

D and E together reconstitute C. Neither adds anything C did not have.

## Predictions, stated before the run

Written as numbers so they can be wrong. Arm C's prediction was 40–55% and the result was 60.4% — the
reading underestimated the rubric's grip, so these are set higher than the same reasoning would
suggest, and that adjustment is itself a claim being tested.

| | rejection recall | false rejection |
|---|---|---|
| **D** | 45–60% | 25–40% |
| **E** | 5–20% | 0–8% |

**Additivity:** if the grounds are independent, D's catches plus E's catches should approximate C's 32,
with modest overlap. Large overlap would mean the judge reaches the same conclusion by either route
and the wording matters less than assumed.

## Decision rule, fixed in advance

| outcome | reading |
|---|---|
| E: recall ≥ 25% with false rejection ≤ 20% | the objective ground is adoptable on its own. Ship it, keep measuring the other |
| E: recall < 25% at ≤ 20% false rejection | safe and thin — worth keeping, not worth claiming |
| D: false rejection ≤ 20% | the cost is NOT in "asserts no defect" as such, and arm C's damage came from the combination. Surprising, and the more interesting result |
| D: false rejection > 20% | confirmed: judging substance from a suggestion's form is where correct findings die. The ground needs a boundary this bench cannot draw by prompt alone |
| D and E both under 20% | then arm C's 38.5% is an interaction, not a sum, and the whole framing needs rethinking |

Both arms are published whatever they say. Comparison is paired against arm A over the same items,
with the uninformative conditions unchanged — including fewer than 10 discordant pairs.

## The line this experiment is close to

Four rubric variants deep, the honest risk is no longer methodological subtlety: it is that varying
the prompt until one lands under 20% false rejection **is** fitting, and dressing it as science by
pre-registering each step does not change that. Two things keep this on the right side of the line:

1. The hypothesis is a **decomposition**, not a new guess — D and E take apart a change already
   measured, rather than searching the space of possible prompts.
2. **This is the last variant.** If neither ground is adoptable, the answer is that a prompt cannot
   separate substantive suggestions from stylistic ones at this sample size, and the next move is a
   different instrument — not a fifth wording.

## Cost

~US$ 1.00 for both, run in parallel.
