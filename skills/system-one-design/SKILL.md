---
name: system-one-design
description: Turn a prompt-and-parse step into typed questions — one condition each, option names that do not carry the verdict, a number back — and never let that number stop the work on its own.
version: 0.2.0
kind: pattern
stage: define
topic: ai-agents
triggers:
- parsing a model's yes or no out of prose
- classifying many items with a model
- asking a model to rate something
- a model answer decides what happens next
provenance: clean
status: active
license: Apache-2.0
---

## Trigger

A step asks a model a question whose answer is one of a few known values — yes or no, one of five
labels, a level from low to high — and then reads that value out of the prose it wrote. Or the same
question is asked of hundreds of items. That is a typed decision wearing a chat costume.

## Do

1. Ask it as a typed question (`chimera decide`, `POST /api/decide`, or the `decide` tool): `noul`
   for yes/no, `choice` for one of a set, `score` for ordered levels. A probability comes back.
2. One condition per question. "Is it an error and from the database?" is two questions; ask both and
   combine the answers in code, where the rule is visible.
3. Give options names that do not carry the verdict (not "yes", "safe", "pass") and put the meaning
   in the criteria. A catch-all option ("other") is where an unsure reading goes — give it a narrow
   criterion or leave it out. Make the options' first words differ: a local model reads the label
   from its first token, so `coding` and `coding_agent` can never be told apart (24 of 231 public
   JevBench items went unread this way).
4. Show the model the thing being judged, alone: not the tool output around it, not a sentence that
   argues for an answer.
5. Pick any threshold from labelled examples, never from a guess, and keep the number beside the
   decision it fed.
6. Keep arithmetic, dates and counts in code: compute the fact, put it in the state, and ask about it.
   A decision model reads; it does not calculate (on JevBench's hard tier the local model got 1 of 15
   date-and-number items right).

## Avoid

Letting the number end, skip or approve work. A decision may add scrutiny — a review, a warning, a
second check — and nothing else: the one decision in this project that could say "stop here" made
every model it steered worse, and worse the better the model was.

Also avoid reading the answer after a reasoning trace (the number collapses to 0 or 1), splitting one
judgment into atoms that each describe something both classes share (a "destroys data?" atom flagged
legitimate cleanups as readily as attacks), and trusting a raw probability as calibrated. Keep the
state lean: a line for every fact that did not fire (a column of "none") moved the number on every
item, and the same content repeated reads as a pattern that is not there.

## Check

- The linter accepts every question (a rejected question never reaches a model).
- Each question answers both ways across your items; one that always says the same thing is reading
  the format, not the text.
- On a long state with a local model, the prompt fits the context window — a silent truncation reads
  as a model result.
- Somewhere, a labelled sample shows the number separates the cases you care about — and it covers
  every way the decision goes wrong, on both sides; a sample from one kind of failure calibrates that
  kind only.
- Before reading a change of wording as an effect, ask the unchanged question again and see how far
  the number moves on its own.

## Risk

A typed answer looks more certain than prose, and it is not: a small model can rank well and still be
confidently wrong in the middle of its scale. Treat an uncalibrated number as a hint, record it, and
calibrate on your own labels before any threshold means anything.
