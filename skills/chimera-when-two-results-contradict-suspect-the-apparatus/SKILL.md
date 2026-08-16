---
name: chimera-when-two-results-contradict-suspect-the-apparatus
description: When two of your own measurements disagree by a margin no mechanism could produce, the defect is in the instrument. Audit the harness before building theory on top of it.
version: 0.1.0
kind: pattern
triggers:
- two runs disagree wildly
- the smaller run scored better
- a metric moved in an impossible direction
- explaining a surprising benchmark result
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

Two measurements you produced yourself disagree by an amount you cannot account for: the run with a
fraction of the data scores better than the big one; a component reads 0% on a task an adjacent
measurement shows it doing routinely; a change moves a number in the direction it makes impossible.

The trigger is the *size* of the gap, not its existence. Two runs differing within their noise band
is a sampling question, and this card has nothing to say about it. It also does not apply to your
number versus somebody else's published number — different data, prompts, seeds and versions explain
those all day, and treating that as an apparatus alarm will have you auditing a harness that is fine.
The case here is: both sides of the contradiction are yours, and both cannot be true.

## Do

1. Write the contradiction down first, as two lines: the two numbers, and the exact commands and
   commits that produced them. Stop theorising until that is on the page — an unwritten contradiction
   gets softened into a puzzle within about a paragraph.
2. Diff the two runs before reasoning about them: config, commit, code path, input file hash, the
   version of the scorer. Prefer a diff you can read over an explanation you can construct.
3. Push known answers through the measurement itself. Run the reference/gold solutions through your
   grader *before* trusting any model score. If a known-correct answer is graded as a failure, the
   defect is in the grader and every number it has produced is void, including the one you liked.
4. Only once the apparatus survives step 3 do you spend effort explaining the phenomenon.
5. If the apparatus was at fault, retract the numbers rather than reinterpreting them. A score from a
   broken scorer is not a noisy estimate of the truth; it is unrelated to it.

## Avoid

Building several careful hypotheses to explain both numbers and then testing them rigorously. That
is the expensive version of this mistake: the rigour is real, the effort is real, and all of it is
measured on an artefact. Method quality downstream of a broken instrument only gets you to the wrong
answer with better error bars.

Avoid reconciling by arithmetic — averaging the two, or quietly keeping the one that matches what you
expected. Avoid a closed-world grader, which is the specific bug that produces this shape most often:

```python
# no — an answer that is correct but unlisted is scored as a failure,
# and the score then reads as "the model cannot do this at all"
CITIES = {"lisbon", "porto"}
ok = answer.lower() in CITIES

# yes — grade against a rule that the real world can satisfy
ok = geocode(answer) == geocode(expected)
```

And avoid the phrase "it must be a fluke" as a stopping point. It is a hypothesis about the
apparatus, so it is testable — test it or drop it.

## Check

One sentence, out loud, with a magnitude in it: *"X produces a difference this large because …"*. If
you cannot finish it — "ten times less data producing a better score because …" — the instrument is
the suspect and step 3 is where you go next.

Then the binary one: did the gold answers pass the grader? Any gold item the grader marks wrong is a
ceiling on what the measurement could ever have meant, and the fraction of them that fail is the
first number to report.

## Risk

Sometimes the contradiction is real and the surprising result is the finding. A reflex of blaming the
harness discards those, and — worse — it is exactly the move available to anyone who wants an
inconvenient measurement to go away. So bound it: the apparatus check is a fixed, short list
(diff the runs, gold through the grader, confirm the input hashes). If that list comes back clean,
believe the contradiction and go investigate the phenomenon. This card orders the work; it does not
pick the answer.

The second cost is recursive. New code written to check the old code is one more thing that can be
wrong, and a buggy audit script has now produced a third number. Prefer checks that use artefacts you
already have and inputs whose answers are known, over a fresh harness written under the pressure of
an anomaly.
