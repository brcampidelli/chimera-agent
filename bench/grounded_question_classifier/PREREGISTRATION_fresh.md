# Pre-registration — a fresh, unseen set for the grounded-question classifier

Written and committed **before** `is_question` was run on the set. US$ 0, no model call in the reading.

## Why

The README's own gap: v2 reads the held-out set at 1.000 only after its rules were widened on that
set's misses, so its one out-of-sample number (0.805 question precision, v1) describes a classifier
that no longer ships. Whether v2 generalizes needs a set it has never seen.

## The set

- `fresh.jsonl`: 160 messages, 80 PT-BR / 80 EN, 40 questions and 40 tasks each.
  sha256 `38dd0731036980cf11842797c5b4845357f18b0cc37870c1c374f86b1417b947`.
- **Written by a different model family**: the Chimera desktop agent on `openrouter/openai/gpt-6-luna`,
  in an empty workspace with no repository, using `write_file` only (receipt: 2 steps, one tool,
  US$ 0.0047). It was given the two class definitions and asked for a third of each class to be
  hard on purpose: tasks phrased as questions, questions phrased as commands. It never saw the
  classifier, its rules, the README or the earlier sets.
- **Labels audited** by the reviewing session before the run. All 160 kept as written, no relabel.
- **Known weakness, stated now:** the EN half is close to a translation of the PT half, item by item.
  The set is ~40 scenarios per class in two languages, not 80 independent ones. The reading below
  is therefore reported both per message and per scenario (a PT/EN pair counts as misread if
  either side is).

## Classifier under test

`chimera/fusion/grounded_question.py` at `6d198d93` (0.63.0), sha256 `ccf9becc…3036`. **No change to it in
this PR.** A fix prompted by this set goes in a separate PR, and from then on this set is in-sample
and is labelled so.

## Readings, fixed now

1. **Primary: tasks read as questions**, of 80. This is the error that can make the gate decline
   legitimate work. Wilson 95% interval reported.
   - **Generalizes** if the upper bound is **≤ 10%**.
   - **Does not generalize** if the point estimate is **> 10%**.
   - **Inconclusive** otherwise.
2. The same count per language, and per scenario (of 40 pairs).
3. Secondary, safe direction: questions read as tasks, of 80. These are questions that ship unchecked,
   the pre-0.63.0 behaviour. They are reported, not judged.
4. Every miss is listed verbatim.

**Prediction:** 0 to 8 tasks read as questions (≤ 10%), with misses, if any, in appraisal questions
("is this fair to me?") and in polite frames, which are the classes v2 widened on the held-out set.

## What this cannot show

The cost of a misread. A task read as a question only hurts if the verifier then declines it.
Measuring that takes a paid run of the gate on tasks with their documents, which is a separate
pre-registration.
