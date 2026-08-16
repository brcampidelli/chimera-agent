---
name: chimera-write-the-check-before-the-code
description: A criterion written after the implementation describes the implementation. Write the failing check first, watch it fail, then build.
version: 0.1.0
kind: pattern
triggers:
- about to implement a feature
- defining what done means
- writing the test after the code
- no acceptance criteria on the task
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to implement something whose "done" is not yet observable — a feature, a bug fix, a
refactor that claims to preserve behaviour. It bites hardest when the author and the only reviewer
are the same process: an agent working alone, or a solo commit nobody else will read.

It does not apply to a spike. Exploration whose purpose is to find out what is even possible has no
acceptance criterion yet, and inventing one upfront just anchors you to the first idea. Write the
check when the spike ends and the real work begins.

This card is about the check that does not exist yet. Its sibling, `chimera-prove-the-test-
discriminates`, is about a check that already exists and may be empty — you reach for that one after
a fix, to show the test fails without it. Same instinct, opposite ends of the work.

## Do

1. Before touching the implementation, write the criterion as something that can fail: a test, an
   assertion, a command whose exit code flips, a query with an expected row. Prose is not a check —
   "the endpoint should be faster" is not; "p95 under 200 ms on the fixture set, measured by the
   existing bench command" is.
2. Say where the observation comes from. The machine, not your reading of the diff.
3. Run the check now, against unmodified code. It must fail. If it passes, either the behaviour
   already exists — in which case stop, there is nothing to build — or the check does not test what
   you think.
4. Read the failure message. It must fail for the intended reason, not on an import error, a missing
   fixture, or a typo in the test name. A red that comes from the wrong cause is a green in disguise.
5. Land the check before the implementation, in its own commit. Once the implementation exists, the
   check becomes editable to match it, and it will be.
6. Implement. Done is when the check passes — not when the code looks finished.

## Avoid

Writing the assertion afterwards, from the output:

```python
# implementation already written; you run it once to see what it does
normalize("  Foo ")        # -> "foo"

# test transcribed from that observation
assert normalize("  Foo ") == "foo"
```

That assertion cannot fail against the code it was copied from. It records behaviour instead of
requirement, so it stays green through every bug the implementation is internally consistent about.
If the requirement was NFKC normalisation and you shipped `strip().lower()`, this test agrees with
you forever.

Avoid also the criterion that is a restatement of the change — "done when the function is added",
"done when the migration runs". Both are satisfied by an empty body.

## Check

Two binary questions, both answerable from the repository:

- Did you watch the check fail before the implementation existed? If there is no moment where it was
  red, you have no evidence it can go red.
- Would the check still be correct if the feature had been built a completely different way? A check
  that names internals — a private call, a log line, an exact SQL string — is pinned to your solution
  rather than to the requirement, and will block the next refactor while catching nothing.

Concretely: `git log` shows the check landing at or before the implementation, and reverting only the
implementation commit turns the suite red.

## Risk

A requirement you do not yet understand cannot be pinned by a check written first. You will write a
precise assertion about the wrong thing and then implement to it — worse than having no check,
because it launders a misunderstanding into a green suite that a reviewer will trust. When the
criterion is genuinely unknown, that is a signal to go ask, one question at a time, not to guess in
test form.

There is also plain cost. For a typo fix or a docs edit, writing the check first is ceremony with no
payer. The card earns its keep when the behaviour is load-bearing enough that someone gets hurt by
"done" being wrong.
