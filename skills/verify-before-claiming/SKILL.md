---
name: verify-before-claiming
description: Before reporting a task as done, run the check that would fail if it were not — an explanation is not a fix.
version: 0.1.0
kind: pattern
stage: verify
topic: software-dev
triggers:
- finished the change
- about to report success
- the fix looks right
- summarising what was done
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to say a task is complete. Anything from "fixed the bug" to "updated the config" to
"the tests should pass now".

It does **not** apply when the task was genuinely to explain, review or investigate something. Those
finish with an answer, and demanding a diff from them is its own mistake.

## Do

1. Name the observable that would be different if the work had happened. A file whose contents
   changed, a command whose exit code flipped, a row that now exists.
2. Check that observable. Actually run the command, actually read the file back.
3. Report what you saw, including the command and its output — not your expectation of it.
4. If nothing observable changed, say that plainly instead of describing the change you intended.

## Avoid

Reporting the *plan* as the *outcome*. The failure reads like: "I updated the handler to check the
token before branching" — fluent, specific, technically accurate about the intent, and describing an
edit that was never written to disk.

It is tempting because a convincing explanation feels like evidence. It is not. The explanation is
produced by the same process that would produce it whether or not the edit landed, so it carries no
information about whether it did.

Also avoid checking something adjacent to the claim: running the full suite proves the suite passes,
which is not the same as proving *this* change did *this* thing. Pick the check that would have
failed before.

## Check

The claim and the evidence describe the same event, and the evidence came from the machine.

Concretely: a `diff` that is non-empty, a test that failed before and passes now, output pasted
rather than paraphrased. If you cannot produce one, the honest report is "I could not verify this",
which is a useful thing to say and takes one sentence.

## Risk

Over-applied, this turns a two-line documentation fix into a ceremony, and there are tasks whose
result genuinely is prose. The cost of the check should stay well under the cost of being wrong.

The subtler risk: a check that always passes is worse than no check, because it launders a guess into
a verified claim. If the verification cannot fail, it is not verifying anything.
