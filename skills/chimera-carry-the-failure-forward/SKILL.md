---
name: chimera-carry-the-failure-forward
description: A retry loop that overwrites its feedback variable shows attempt 3 only the failure of attempt 2 — so it re-derives the patch attempt 1 already tried.
version: 0.1.0
kind: pattern
stage: build
topic: ai-agents
triggers:
- writing a retry loop
- the agent keeps trying the same fix
- feeding verifier output back to the model
- attempts keep failing the same way
- reverting the workspace between attempts
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

You have a loop that runs an attempt, judges it, and runs another one with feedback: an agent with a
verifier, a code fixer against a test suite, a generate-and-check pipeline. The budget is more than
two attempts, and the workspace is reverted between them so each attempt starts clean.

It does not apply to an idempotent retry over a flaky transport — a network call that failed for
reasons unrelated to what you sent. There is nothing to learn from attempt 1 there, and carrying it
forward is just payload.

## Do

1. Store attempts in a list, not in a variable you reassign. One record per attempt: the verifier
   output, the patch the attempt actually wrote, and which tool step errored first.
2. Take the patch from the workspace snapshot, before the revert — the real diff, not the model's
   description of what it changed. The revert is what makes this necessary: once the tree is rolled
   back, nothing on disk records the wrong path either, so the next attempt has no obstacle to
   re-deriving the same one.
3. Compose the retry prompt from every record, oldest first, each labelled with its attempt number
   and its verdict, under a heading that says these were already tried and reverted.
4. Bound each record. Chimera caps a fed-back diff at 2000 characters and truncates with a marker;
   an unbounded three-attempt history will dominate the prompt.
5. Deduplicate by failure signature. If two attempts failed the same way, keep one and record that
   it recurred — the repetition is the signal, the second copy is not new information.
6. When the signature repeats, stop appending and change something structural: re-plan with the
   accumulated causes as planner context (`TaskLedger.context()` renders them under "Why earlier
   attempts failed (do NOT repeat these):"), or escalate to a stronger model. More feedback about the
   same dead end does not leave the dead end.
7. Emit a countable event each time you inject history, so a benchmark arm can prove the injection
   fired at all.

## Avoid

The overwrite. In `chimera/core/autonomous.py` the loop's feedback is rebuilt each round:

```python
feedback = "\n\n".join(p for p in (fb, _verify_fb) if p) or "The attempt did not pass verification."
```

so attempt 3 is composed with attempt 2's failure and nothing from attempt 1. Carrying it forward
means appending to a structure instead — sketched, not quoted, because the accumulating version is
what this card argues for rather than what the file currently does:

```python
records.append(record_for(index, verdict, patch))   # one entry per attempt
feedback = render(records)                          # every attempt, oldest first
```

Be precise about which half is already there when you read this in Chimera: the *content* of the
feedback is well covered — `--diff-feedback` shows the retry the patch it actually wrote
(`chimera/core/autonomous.py`, capped at `_DIFF_FEEDBACK_MAX_CHARS = 2000`), and `TaskLedger`
accumulates causes for the planner. What is not covered is the *accumulation across attempts* in the
line above. A card that presented all of it as missing would be arguing against work already done.

Also avoid telling the retry *that* it failed without showing *what* it wrote. "The attempt did not
pass verification" plus a failing test is enough to make a model try again and not enough to make it
try something different — the wrong patch is invisible to it and the workspace no longer contains
it, so re-deriving it is the path of least resistance.

And avoid carrying only the manager's prose while dropping the verifier's output. The failing assert
is the most actionable line in the whole loop; a reviewer's paragraph about it is a paraphrase of
strictly less information.

## Check

Instrument the composer, run a task you know fails three times, and grep the attempt-3 prompt for a
string that exists only in attempt 1 — a filename it touched, an identifier from its diff. Present
or absent; there is no partial credit.

Second check, equally binary: count the injections. A run where the history was never assembled
because the guard was wrong, or because the diff list was empty, measured nothing at all — and a
benchmark arm built on that measures the plumbing, not the idea. If the counter reads zero, the
result is void regardless of what the success rate says.

## Risk

Anchoring is the registered counter-hypothesis, not a hypothetical: showing a model the wrong patch
can fix its attention on that patch and produce variations of a dead approach instead of a different
one. This is why the behaviour is opt-in and measured in Chimera (`--diff-feedback`) rather than
turned on by default. Treat it as a claim to test on your own tasks, not a settled improvement.

The second cost is the context budget. Three diffs, three verifier dumps and a manager review can
push the prompt past its compaction threshold — and then a compactor with no restoration will drop
exactly the accumulated history you paid to build. Bound the records and know your budget before
enabling this, or the two mechanisms will fight and the visible symptom will be neither of them.
