---
name: pin-the-case-that-narrowed-the-rule
description: When a check fires on the wrong target, the fix makes it weaker. Capture the false positive as a test in the same commit, or the rule erodes silently.
version: 0.1.0
kind: pattern
stage: verify
topic: software-dev
triggers:
- the linter flagged something legitimate
- adding an exception
- false positive in CI
- relaxing a check
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

A check you wrote has failed on something that is actually fine, and you are about to add an
exemption, an allowlist entry, or a narrower pattern.

It does not apply when the check found a real problem — fix the problem. It also does not apply to
tuning a threshold before the check has ever run in anger.

## Do

1. Write down, in one sentence, why the flagged case is legitimate. If you cannot, the check may be
   right and the code wrong.
2. Add **both** cases to the test suite: the legitimate one that must pass, and a synthetic version
   of the real violation that must still fail.
3. Narrow the rule as little as possible. Exempt a path, not a whole directory; require a negation
   nearby, rather than deleting the phrase from the list.
4. Put the narrowing in its own commit, and say in the message which case forced it.

## Avoid

Deleting the rule because it was annoying once. Also avoid the quieter version: broadening an
exemption until the rule covers nothing — an allowlist that grows every sprint is a rule being
retired one line at a time, without anyone deciding to retire it.

Avoid exempting by *file* when the real distinction is by *meaning*. A phrase check that bans a
sentence outright will ban the warning that negates it, and the honest fix is to require the
negation, not to stop checking that page.

## Check

After narrowing, run the suite with the original violation reintroduced. It must still fail.

Then read the diff of the rule itself and ask: what class of problem can now get through that could
not before? If you cannot answer, the narrowing was not understood.

## Risk

This adds a test for every exception, and a suite full of exception tests is a suite that is
tedious to read.

The bigger risk is treating the pinned test as proof the rule is still strong. It proves one case
still fails. A rule narrowed five times has five pinned cases and possibly a large hole between
them, and only re-reading the rule as a whole will show that.
