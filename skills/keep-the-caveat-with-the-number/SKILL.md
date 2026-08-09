---
name: keep-the-caveat-with-the-number
description: A figure and its qualification must be one artifact. Two paragraphs drift apart; one component cannot.
version: 0.1.0
kind: pattern
triggers:
- publishing a benchmark result
- writing a metric into a page
- the number needs context
- summarising a measurement
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are putting a measured figure somewhere a reader will see it: a README, a landing page, release
notes, a dashboard, a report. The figure is true and it needs a sentence beside it to not mislead —
the sample was small, the slice was easy, the effect was not significant, the benchmark was ours.

It does not apply to a figure whose meaning is complete on its own. "Build took 41 seconds" needs
nothing.

## Do

1. Write the caveat first, before the number. If you cannot state the limitation in one sentence,
   you do not yet understand the measurement well enough to publish it.
2. Make the caveat *structurally attached*. Put it in the same component, the same table row, the
   same function return — something that cannot render the figure without it.
3. Put it **above** the number, or beside it. Not below.
4. Make the number itself derived: read it from the artifact the measurement produced, so it moves
   when the measurement moves.

## Avoid

The number in the headline and the caveat in a footnote, an asterisk, a collapsed section, or the
paragraph after. Those are all the same bug with different styling: the reader forms the belief
first, and a correction arriving afterwards has to overcome a belief that already exists.

Avoid also the second-order version — a component that *can* render the figure with the caveat
omitted. If the qualification is an optional argument, it will be omitted, and it will be omitted
by someone tightening a paragraph who never intended to change the meaning.

And do not paraphrase a caveat that was written carefully. Rewording is where "not significant on
its own" becomes "close to significant".

## Check

Try to write the number without the caveat and watch it fail. Delete the qualification argument,
or put a bare figure in a page, and run the build.

If it compiles, the pairing is a convention rather than a mechanism, and conventions survive
exactly as long as nobody is in a hurry.

## Risk

Over-applied this makes ordinary reporting heavy: not every number is a benchmark, and a caveat on
a figure that does not need one trains people to skip caveats.

The harder risk is that a mechanism *feels* like the whole answer. A component that always renders
some caveat does not check whether the caveat is the right one. The sentence still has to be
written honestly by a person; this only stops it from being dropped afterwards.
