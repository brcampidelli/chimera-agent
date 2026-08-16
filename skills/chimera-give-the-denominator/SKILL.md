---
name: chimera-give-the-denominator
description: A rate without its denominator and its composition misleads while every digit stays true — say n, and say how much of the numerator is already-known noise.
version: 0.1.0
kind: pattern
triggers:
- reporting a failure rate
- a percentage in a summary
- the number looks alarming
- comparing before and after a change
- writing a benchmark result
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to put a rate, a percentage, or a count into a report, a commit message, a
benchmark table, or a message to someone who will act on it. "34% of runs failed." "Errors up
3x." "Coverage dropped."

It does not apply to a number the reader can already decompose: a single measured latency, a
version string, a count of files in a diff they can see. The risk is specific to an aggregate —
a quantity computed by dividing one set you chose by another set you chose, where both choices
are invisible in the sentence.

## Do

1. Write the raw counts next to the rate, always: `34% (17/50 runs)`. If you cannot state the
   denominator, you do not yet know what you measured.
2. Say which denominator. Attempted runs and completed runs are different sets, and dividing by
   completed silently deletes the crashes — the exact population that made you look.
3. Split the numerator by cause before publishing it. Of those 17 failures, how many are an
   already-open issue, a known flaky provider timeout, a fixture that fails on this machine?
   Report the split: `17 failed — 14 are the known connect timeout, 3 unexplained`.
4. Run the same measurement against an unchanged baseline and subtract. The number that matters
   is the part that does not survive on `main`.
5. When n is small enough that one more sample would move the headline figure by several points,
   put that sentence in the report instead of the percentage.

## Avoid

`"failure rate 34%"` — true, and it invites the reader to spend a day on a timeout they already
filed last week. Write `"17/50 failed; 14 are the ollama connect timeout (issue open), 3 are new"`
and the day gets spent on the 3.

Avoid the survivorship denominator. `rate = failures / completed` is the classic: the runs that
died before producing a row are absent from both sides, so the metric improves as the system gets
worse.

Avoid percentages over a denominator under about twenty without n attached. `50% → 67%` is a real
improvement or it is one run out of three flipping, and the two are indistinguishable on the page.

Chimera has published a retraction of exactly this shape, and it is the reason this card exists.
`bench/learning_lift/RESULTS.md` reported a significant within-family transfer gap of **+6.7%**, and
run 7 — the same measurement at adequate power — did not hold it. The number was real, the
arithmetic was right, and it was still best explained as a small-sample fluctuation. What made the
retraction possible was that the run count had been published beside the percentage from the start.
Had the page carried only "+6.7%", there would have been nothing to re-examine, and the finding
would have quietly become a fact.

## Check

Hand the sentence to someone who cannot see your data and ask them to reconstruct the raw
counts. If they cannot, the sentence is incomplete — this is a binary test and it takes ten
seconds.

Second check, when the number is driving a decision: re-run the measurement on an unmodified
baseline. Whatever fraction of the numerator reproduces there is not about your change, and any
claim built on it is about the harness.

## Risk

Applied to everything, this turns readable prose into a parenthesis swamp, and readers start
skipping the parentheses — which is worse than the original problem, because now the denominators
are on the page and unread. Reserve the full decomposition for numbers someone will act on.

The sharper risk runs the other way: cause-splitting is also the tool for making a real problem
look small. Labelling fourteen failures "known noise" is honest only if the label was assigned
before you saw which way it helped. If you find yourself widening the "known" bucket while
writing the summary, the number was the conclusion, not the evidence.

A stated denominator also does not rescue a bad one. `2/3 (n=3)` is still three runs; the
denominator makes the weakness visible rather than fixing it.
