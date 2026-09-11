# Pre-registration — on problems the judge cannot solve alone, does its verdict follow the name or the position?

**Registered 2026-09-11 against `b686514` (main after #434), before a single model call.**

## Where the question comes from

`bench/judge_blind` (PR #432) asked whether the fusion judge's verdict follows the vendor name and
the position it is shown. It could not answer: on GSM8K the production judge
(`deepseek-v4-flash-0731`, a reasoning model) re-derived the arithmetic itself in 169 of 240
analyses and got 240/240 with the names and 119/120 without them. A judge that solves the task
does not consult the labels. arXiv 2609.08016's judges could **not** solve the task, and that is
where the bias lives. This is the follow-up `bench/judge_blind/RESULTS.md` named: a corpus the judge
cannot solve alone.

## The corpus

`AI-MO/aimo-validation-aime` — 90 AIME problems (2022–2024), Apache-2.0, integer answers,
fetched at run time from the Hugging Face datasets-server API (not vendored). Three writers from
three vendors, none of them the judge, each asked once at temperature 0.3 with a 16k-token
completion budget: `openrouter/z-ai/glm-5.3-flash`, `openrouter/openai/gpt-oss-20b`,
`openrouter/moonshotai/kimi-k2`. Graded by exact integer match on the `ANSWER:` line (or the last
integer in the text).

**The filter that makes this the right instrument, first:** the judge alone, at its production
temperature 0.1, answers every problem **twice**; an item is kept only if the judge was wrong
**both** times, at least one writer was right, at least one was wrong, and the three texts are
distinct. Items the judge can solve are excluded by construction — they are the ceiling the last
run sat on. The number excluded is reported.

## Conditions — unchanged from `bench/judge_blind`

Six named runs in the balanced design (every text under every production name twice, at every
position twice), three blind runs (`FusionConfig.blind_panel=True`, the shipped default), all
through the production `FusionEngine._aggregate`. Judge: `deepseek-v4-flash-0731` at 0.1.

*Synthesiser amended for cost, stated here:* the production synthesiser is `claude-opus-5`; with
three long solutions per prompt and ~300 runs, that is a US$ 15–20 measurement of a stage that only
follows the judge's analysis. The synthesiser here is `deepseek-v4-flash-0731`. The stage under
test is the judge; the synthesiser is held fixed across arms, which is what the comparison needs.

## Outcomes

Exactly the last run's, now readable: accuracy by arm; **vendor effect** (accuracy when the correct
text carried `claude-opus-5` / `gpt-5.5` / `gemini-3.8-flash`); **position effect** (accuracy when
the correct text sat at 1 / 2 / 3), both arms; paired named → blind on per-item majority; the
fraction of judge analyses that name a vendor; tokens and USD.

## Registered prediction

Now that the judge has to choose: the text labelled `claude-opus-5` is trusted more — accuracy when
the correct text carries that name exceeds accuracy when it carries `gemini-3.8-flash` by
**≥ 10 pp**; position 1 beats position 3 by ≥ 5 pp in the named arm; the blind arm shows no vendor
gap (it cannot) and a position gap no larger than the named arm's; blind accuracy is within ±5 pp
of the named mean.

## Decision rule

`blind_panel` is already the default (its cost was measured at zero twice). This run decides what
the changelog *says*: a vendor gap ≥ 10 pp with non-overlapping Wilson intervals is published as
**bias measured in the shipped configuration** that the default removes; anything smaller is
published as a null with the intervals. If blind accuracy is worse than named by more than 5 pp,
the default is revisited and the number published.

## Cost

Writers: 90 × 3 long calls ≈ US$ 2. Judge-alone: 180 short-context calls ≈ US$ 0.3. Pipeline:
~30 items × 9 runs × 2 calls with three solutions in context ≈ US$ 1.5. **≈ US$ 4.**

## What this cannot show

AIME is in every modern model's training data; the filter keeps only what the judge still cannot
do, which is a biased slice of a contaminated set — fine for "does the judge choose by name",
wrong for any statement about AIME accuracy. One judge, one synthesiser (not the production one),
writers weaker than the production panel under its names. Integer answers: nothing here bears on a
fused prose turn, where the paper's bias may be larger and nothing is checkable.

## Amendments

1. *(2026-09-11, after 50 of 90 problems were asked, before any pipeline call.)* The collection is
   stopped at 50. Zero items met the filter: the judge fails both attempts on 7 of 50, and on those
   seven the reasoning writers return an empty text on 13 of 21 slots (the reasoning spends the
   16k budget — the runaway `bench/spec_test_vacuity`'s probe measured the same day). The two
   clauses of the filter select against each other, and the projection at 90 was zero or one. The
   remaining ≈ US$ 2 is not spent. The pipeline is not run; RESULTS.md reads the fifty rows.
