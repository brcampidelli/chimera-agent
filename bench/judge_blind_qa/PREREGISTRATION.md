# Pre-registration — on facts the judge does not know, does its verdict follow the name or the position?

**Registered 2026-09-11 against `5aba3ba` (main after #439), before a single model call.**

## Where the question comes from

`bench/judge_blind` (#432) asked whether the fusion judge's verdict follows the vendor name and the
position it is shown, and hit a ceiling: on GSM8K the reasoning judge re-derives the arithmetic
(240 / 240 named, 119 / 120 blind). `bench/judge_blind_hard` (#439) tried AIME and could not build
the corpus: on the problems the judge fails, the reasoning writers run away and return nothing,
because the filter's two clauses both select for difficulty. RESULTS.md there priced the next
instrument: **a judge that cannot re-derive** — a closed-book factual corpus, where a writer either
knows the fact or does not, nothing can be computed, and answers are short.

## The corpus

SimpleQA (OpenAI `simple-evals`, MIT), 4,326 short factual questions with a reference answer,
fetched at run time from the published CSV (not vendored). A seeded sample of up to 400 questions
whose reference is at most five tokens. Three writers from three vendors, **none reasoning models**
(the runaway #438 and #439 measured is a reasoning-model failure): `moonshotai/kimi-k2`,
`meta-llama/llama-3.3-70b-instruct`, `google/gemma-3-27b-it`, each asked once at temperature 0.3
with a 300-token budget and the instruction to end with `ANSWER: <the answer, as short as possible>`.

**Grading is deterministic and strict, and the same for every text:** the `ANSWER:` line is
normalised (case, accents, punctuation, thousands separators) and a text is right when every token
of the normalised reference appears among the answer line's tokens and the line has at most twelve
tokens (a list of candidates is not an answer). Strict cuts both ways symmetrically — a writer that
gives the surname alone is wrong — and the bias question is a *relative* one on the same items, so
a strict grader widens intervals and does not move the comparison.

**The filter, the same as `judge_blind_hard`'s:** the production judge (`deepseek-v4-flash-0731`,
temperature 0.1) answers each question twice, closed-book; an item is kept only if the judge was
wrong **both** times, at least one writer was right, at least one was wrong, and the three texts
are distinct. Collection stops at 40 kept items or at the end of the sample; the number examined
and the number excluded by each clause are reported.

## Conditions — unchanged from `bench/judge_blind`

Six named runs in the balanced design (every text under every production name twice, at every
position twice), three blind runs (`FusionConfig.blind_panel=True`, the shipped default), all
through the production `FusionEngine._aggregate`. Judge: `deepseek-v4-flash-0731` at 0.1. The
synthesiser is the judge model, for the reason `judge_blind_hard` registered: the stage under test
is the judge and the synthesiser is held fixed across arms. The pipeline's final answer is graded by
the same rule as the writers.

## Outcomes

Accuracy by arm; **vendor effect** (accuracy when the correct text carried `claude-opus-5` /
`gpt-5.5` / `gemini-3.8-flash`); **position effect** (accuracy when the correct text sat at 1 / 2 /
3), both arms; paired named → blind on per-item majority; the fraction of judge analyses that name
a vendor; tokens and USD.

## Registered prediction

With nothing to re-derive, the judge chooses by plausibility and by label: accuracy when the correct
text carries `claude-opus-5` exceeds accuracy when it carries `gemini-3.8-flash` by **≥ 10 pp**;
position 1 beats position 3 by ≥ 5 pp in the named arm; the blind arm shows no vendor gap (it
cannot) and a position gap no larger than the named arm's; blind accuracy is within ±5 pp of the
named mean. Written down so it can be wrong.

## Decision rule

`blind_panel` is already the default. This run decides what the changelog *says*: a vendor gap
≥ 10 pp with non-overlapping Wilson intervals is published as **bias measured in the shipped
configuration** that the default removes; anything smaller is a null with the intervals. If blind
accuracy is worse than named by more than 5 pp, the default is revisited and the number published.
If fewer than 20 items are kept, the run is made anyway and read as a direction, and the count is
the headline.

## Cost

Collection: ≤ 400 × (3 writers + 2 judge-alone) = 2,000 short calls ≈ US$ 0.6. Pipeline: 40 items
× 9 runs × 2 calls with three short answers in context ≈ US$ 1. **≈ US$ 1.6.**

## What this cannot show

Closed-book trivia on one judge; nothing here bears on a prose turn where nothing is checkable,
where the paper's bias may be larger. The writers are weaker than the production panel whose names
they carry. A strict grader misgrades some right answers as wrong on both sides. Contamination is
not the concern it was for AIME (the questions are obscure by construction), but the judge may
*recognise* a fact it could not *recall* — that is part of the mechanism under test, not a defect of
the instrument.


## Addendum — the same 38 items with the judge and synthesiser bounded (registered 2026-09-12, after RESULTS.md, before any call)

RESULTS.md recorded what an unbounded reasoning judge costs: 29 of 341 runs (8.5%) ran past 16k
completion tokens — one to 146k — carrying **71%** of the run's cost, one run of 3,096 s, one halt
after forty minutes, and 8 of those 29 passing where the rest passed two in three. The same
runaway the spec-test generator (#438) and the AIME writers (#439) showed.

**The change under test** (`feat/completion-ceiling`): `FusionConfig.judge_max_tokens` and
`synth_max_tokens`, 16,000 each — the far end of what a converged reply needed (median 1,901,
90th percentile 12,628) — with one retry on an empty reply (twice the budget after `length`),
and `Settings.completion_ceiling` (32,000) applied by the gateway to every call whose caller set
no budget. The provider's `finish_reason` goes on the trace and the route meta.

**The measurement.** The same 38 items, the same nine runs each, the same judge and synthesiser,
through the bounded engine — `results/2026-09-12-runs-capped.jsonl` against the unbounded
`results/2026-09-12-runs.jsonl`. Read per run: seconds (median, p95, max), completion tokens,
US$; per arm: accuracy, and per item the paired difference against the unbounded run.

**Predictions.** p95 seconds falls from 1,000 to under 300 and the maximum from 3,096 to under
600; mean cost per run falls by **≥ 40%** (bounding the runaways at 16k + 16k removes most of the
71%); accuracy stays within **±5 pp** of the unbounded run in both arms (named 0.62, blind 0.73);
the number of retried stages (an empty first reply) is under 5% of runs.

**Decision.** The cap ships whatever the cost numbers say — it is a bound on spend, and a run
that costs less and takes a tenth of the time at the same accuracy is the point. If accuracy falls
by more than 5 pp in either arm, both fusion budgets move to 32,000 and the measurement is
repeated before merging; the number is published either way. If the retry fires on more than 5%
of runs, the budget is too low for this judge and the same rule applies.

**Cost.** 342 runs × 2 bounded calls ≈ US$ 0.3 (against US$ 0.63 unbounded).

**What this cannot show.** One judge, one corpus; a route change between the two runs sits inside
the comparison (both runs are on consecutive days), which the per-item pairing partly absorbs. A
16k budget is measured against replies that never needed it on this corpus; a task that does need
more than 16k of reasoning is not represented here.
