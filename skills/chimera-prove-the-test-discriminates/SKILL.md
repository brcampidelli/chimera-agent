---
name: chimera-prove-the-test-discriminates
description: A regression test you never saw fail is a guess. Re-break the code and watch it go red before you commit it.
version: 0.1.0
kind: pattern
stage: verify
topic: software-dev
triggers:
- wrote a regression test
- fixed a bug and added a test
- the test passes, ship it
- adding a test after the fix
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You fixed a bug and wrote a test so it cannot come back. The test passes. That is the whole
evidence you have, and it is the same evidence you would have if the test asserted nothing about
the bug at all.

It applies hardest when the test was written *after* the fix, because then the only code the test
has ever run against is code where the bug is already gone. It applies less to a test written
first, red, in a TDD loop — you already watched that one fail, which is the entire point of the
loop.

It does **not** apply to a test for genuinely new behaviour, where there is no "before" to revert
to. There, the discriminating check is different: delete the new implementation and confirm the
test fails on the absence, not on the bug.

## Do

1. Identify the exact edit that constitutes the fix — the hunk, not the commit. If the fix is one
   changed comparison, that comparison is what you are going to undo.
2. Undo it. `git stash` the fix, or invert the one line back to its broken form by hand.
3. Run only the new test: `pytest tests/test_thing.py::test_the_regression -x`.
4. Read the failure. It must be **your assertion** failing, with a message about the behaviour —
   not a `SyntaxError`, not a collection error, not an `ImportError` from a half-reverted file.
   Those are red for the wrong reason and prove nothing about the test.
5. Restore the fix (`git stash pop`) and confirm the same test now passes.
6. Put the fact in the test's docstring: what was reverted, and what the failure looked like. The
   next person to touch this test needs to know it was ever exercised against the bug.

## Avoid

Asserting on something true in both worlds. The classic shape:

```python
# WRONG — passes against the buggy code too
result = parse_config(raw)
assert result is not None
assert "timeout" in result
```

The bug was that `timeout` came back as the string `"30"` instead of the int `30`. Both assertions
hold either way. What discriminates is the thing that changed:

```python
# RIGHT — fails against the buggy code
assert parse_config(raw)["timeout"] == 30
assert isinstance(parse_config(raw)["timeout"], int)
```

Also avoid reverting by commenting out a whole function or deleting an import to "make it fail
fast". That produces a red run that would have happened for any test in the file, so it tells you
nothing about *this* one. The revert has to be the fix and only the fix.

And avoid the near-miss where the test reaches the fixed code through a path production never uses
— that is a different failure, and the card for it is `chimera-test-the-wiring-not-the-class`.

## Check

One binary question: **did you personally see this test print a failure caused by its own
assertion, against the unfixed code?**

If yes, you are done. If the answer is "it would have failed", you have not checked anything —
that sentence is a prediction produced by the same reasoning that wrote the test.

Mechanically:

```bash
git stash                 # remove the fix
pytest tests/test_x.py::test_regression   # MUST be red, on your assertion
git stash pop             # restore
pytest tests/test_x.py::test_regression   # MUST be green
```

Red-then-green, both observed, or the test is unproven.

## Risk

Some fixes cannot be cheaply reverted: a dependency upgrade, a schema migration, a deleted file, a
change in a vendored library. Forcing a revert there can cost more than the test is worth. The
honest fallback is to reconstruct the *input* that used to break it and assert the specific value,
then say plainly in the docstring that the red run was not observed — an unproven test that admits
it is unproven is far better than one that implies it was checked.

The real hazard of this card is the revert you forget to undo. Doing this dance in a dirty working
tree is how a re-broken line gets committed alongside its own regression test, with the suite
green because you restored the fix in the wrong file. Run `git diff` before committing, every
time.

And a discriminating test is still only as good as the bug you understood. It proves the test
catches *this* reversion. It does not prove you fixed the root cause rather than the symptom, and
treating a red-then-green run as proof of that is a bigger claim than the evidence supports.
