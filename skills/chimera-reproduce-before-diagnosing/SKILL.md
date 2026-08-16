---
name: chimera-reproduce-before-diagnosing
description: A diagnosis is a claim about a machine you have not run. Reproduce the failure in one short command first — especially when the diagnosis arrived from someone else.
version: 0.1.0
kind: pattern
triggers:
- a bug report arrived
- another agent explained the cause
- the traceback points at a file
- about to fix something you have not seen fail
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You are about to change code because of a failure you have not personally watched happen: a red CI
job, a log excerpt in an issue, a user's description, or — the case this card is really about — a
confident diagnosis handed to you by a reviewer, a sub-agent, or a static analysis tool, complete
with a file and a line number.

It does not apply when the failure already *is* one command. A test that fails locally when you run
it is a reproduction; do not build a second one. It also does not apply to work that has no failure
in it — a new feature, a refactor, a design question. Those have nothing to reproduce.

## Do

1. Write the shortest thing that fails and can be run by itself: one script, one pytest node id, one
   CLI invocation. "Run the suite and look at the third error" is not it — you will read past the
   error every time.
2. Run it. Copy the real output into your notes: the exception type, the wrong value next to the
   expected one, the exit code. Not your summary of it.
3. Now state the diagnosis, and immediately try to kill it. Put a `raise RuntimeError("here")` at the
   top of the function the diagnosis blames and rerun the reproduction. If the original failure still
   appears unchanged, that function is not on the failing path and the diagnosis is wrong regardless
   of how well argued it was.
4. Fix, rerun the same command, and keep the reproduction as a test in the same change. A repro that
   is deleted after the fix cannot tell you when the fix is reverted.

## Avoid

Editing the frame of the traceback you recognise. The frame you recognise is the one you have read
before, not the one that is wrong, and a plausible edit there will often make the symptom move
somewhere else — which then reads as progress.

Avoid inheriting a diagnosis as a fact. A handed-over explanation is text, and the process that
produces a fluent wrong explanation is the same process that produces a fluent right one, so the
fluency carries no information about which you got. Treat it as a hypothesis with a name attached:
useful for ordering what to try, worthless as evidence.

```python
# no — the theory is now load-bearing and untested
# reviewer says the cache key is missing the tenant id
key = f"{tenant.id}:{user.id}"

# yes — first make the failure appear on demand
# repro.py: two tenants, same user id, assert the second read misses
```

And avoid the rerun-until-green reflex on an intermittent failure. Rerunning does not diagnose a
race, it hides one, and it converts a reproducible-once bug into a bug nobody can reproduce at all.

## Check

Before you write the fix, answer yes or no: *can I make this failure happen again, right now, with
one command?* If no, you are not debugging, you are speculating with an editor open.

After the fix: does the same command now pass, and did you see it fail beforehand with your own
eyes? A fix validated only by the suite going green proves the suite is green — which it may have
been for the wrong reason, if the failing case was never in the suite.

## Risk

Some failures are genuinely expensive to reproduce: a race that appears once a day, a state that
exists only in production, a crash six hours into a long job. Insisting on a cheap reproduction there
burns more than the bug costs. Time-box it, and if the box expires, say plainly that the fix is
unverified rather than describing it as confirmed.

The subtler trap is over-minimisation. A stripped-down script can start failing for a *different*
reason than the original, and then you fix the toy. Guard against it by applying the fix to the
original failing path too, and confirming the original symptom disappears there — the minimised case
is a tool for finding the cause, never the proof that it was the cause.
