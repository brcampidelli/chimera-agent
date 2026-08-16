---
name: warn-before-the-thing-you-are-warning-about
description: A caution placed after the action it concerns arrives after the reader has already decided. Put it above, and make it undismissable when it matters.
version: 0.1.0
kind: pattern
stage: ship
topic: software-dev
triggers:
- writing a download page
- adding a destructive action
- documenting a footgun
- where should this warning go
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

Something a user is about to do has a consequence they will not expect: an unsigned installer their
OS will block, a command that rewrites history, a setting that removes a human from a loop, an
export that leaves the sandbox.

It does not apply to consequences that are obvious from the label. "Delete" does not need a
paragraph explaining that it deletes.

## Do

1. Put the caution above the control, in the reading order, not below it and not in a tooltip.
2. Say what will happen in the user's terms — the exact dialog text they will see, the exact error
   — and then what to do about it.
3. Reserve interruption for the irreversible. A confirmation on a narrowing action trains people to
   click through the confirmation that matters.
4. If the surprise is unavoidable and benign, explain *why* it happens. "There is no code-signing
   certificate" converts an alarming dialog into an expected one.

## Avoid

Warnings after the button, warnings in a collapsed section, and warnings that a user can dismiss
before they have met the situation. All three are the same failure: the information arrives after
the decision.

Avoid vagueness that sounds responsible. "Use with caution" tells the reader nothing they can act
on, and its real function is to protect the author rather than the user.

Avoid warning about everything. A page where five things are flagged has flagged nothing, and the
one genuinely dangerous item is now camouflaged.

## Check

Read the page top to bottom as someone who has never seen it, and stop at the moment you would
click. Was the caution above that point?

Then check the inverse: count the warnings on the surface. If there is more than one prominent one,
decide which is the real one and demote the rest.

## Risk

Front-loading cautions makes a page feel heavier and can scare off a user for whom the risk was
never real. That cost is genuine and worth paying only where the surprise is genuine.

The failure mode of over-applying this is a product that reads as nervous about itself. If every
screen opens with a disclaimer, the disclaimers become chrome and the reader skips them — which
puts you back where you started, with worse copy.
