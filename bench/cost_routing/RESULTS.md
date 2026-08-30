# Results — stage 1. The corpus cannot answer the question, and stage 2 is not run.

Run 2026-08-29 · 30 tasks · one seed · the shipped model alone · prereg `573dfdde34c5`.

**Read `PREREGISTRATION.md` first.** The arms, the non-inferiority margin, the band and the
conditions for stopping were fixed there before the first model call. This file reports what stage 1
bought, and what it bought was a decision: **do not pay for stage 2.**

## The number

| | |
|---|---|
| shipped model (`deepseek-chat-v3.1`) | **96.7%** — 29 of 30 |
| crashed cells excluded | **0** |
| registered band | 20% – 90% |
| **verdict, by the rule fixed in advance** | **STOP** |

Stage 1 cost **$0.3228** across 2,186,498 tokens and 98 minutes; median 133s per task.

## Why 96.7% ends it

The registered design is a **non-inferiority** test: the cheap tier is adopted if it is not worse
than the frontier one by more than 10 pp. At 96.7% there is no room for that comparison to mean
anything — the most a flash arm could lose is 3.3 points, so the margin would be satisfied by the
ceiling rather than by the models, and a result reading *"cheap is non-inferior"* would be a fact
about `local_lift` and not about routing.

The 270 cells of stage 2 would have cost real money to produce a number that was determined before
the models were chosen. **The corpus, not the models, is the limit — and that is the finding.**

## This is the fifth ceiling

| corpus | measured | for |
|---|---|---|
| `local_lift` (first 5) | 100% | `claude-opus-5`, `gpt-5.5`, `gemini-3.1-pro` |
| GSM8K (logic slice) | 100% oracle, 97.5% majority | the 3-model panel |
| learning-lift suites | 84–92%, three attempts at a 40–60% band | the goldilocks tier |
| **`local_lift` (first 30)** | **96.7%** | **`deepseek-chat-v3.1`, the cheap default** |

The pattern is now consistent enough to plan around, and it is sharper than "our benches are easy":
**`local_lift` no longer discriminates even at the bottom of the ladder.** It was authored to show
what scaffolding adds to a weak model, and the cheap default has since caught up with it. A suite
stops being an instrument when the thing it was calibrated against moves.

## The one task that failed, and what it says

`query_string_parse` — three attempts, 464s, exit 1, no crash. The final answer claimed the fix
worked:

> *"…should now pass, including the previously failing `test_decoding()` which checks that
> `'q=hello+world%21'` correctly parses to `{'q': ['hello world!']}`."*

The gate disagreed. That is the whole reason the verdict is an independent pytest run and never the
arm's self-report, and it is worth one line here because it is the single discriminating task in
thirty: percent-decoding combined with `+`-as-space is the one case in this slice where a confident
wrong answer survives everything except execution.

## What this does NOT say

- **Nothing about routing.** The question stands unanswered. Nothing here supports or refutes
  sending gated work to a flash tier; it says this corpus cannot be used to ask.
- **Nothing about the four projects.** The 105× / 51× / 65× ratios measured there are still one run
  per model on four briefs, and still not evidence for a default.
- **Nothing about the cheap model being good.** 96.7% on a suite that three frontier models also
  ceiling is 96.7% on an easy suite.

## What a corpus would have to be to answer this

The band exists because a non-inferiority margin needs room underneath it. To ask this question at
all, the shipped model has to fail 20–80% of the tasks — and this project has now failed five times
to author such a suite, which is itself the reason its own roadmap moved to SWE-bench Verified:
difficulty nobody here authored, and a grader nobody here owns.

The honest next step for this experiment is not a sixth authored suite. It is to run these three
arms against a corpus whose difficulty was not chosen by the person hoping for a result.
