# Results — the corpus could not be built: on what the judge cannot solve, the writers return nothing

Run 2026-09-11 · prereg `PREREGISTRATION.md` (one dated amendment, below) · raw
`results/collect-all.jsonl` (every problem's three writer texts and two judge-alone attempts, kept or
not) · `results/items.jsonl` (the corpus: **empty**) · US$ 2.71 of the ≈ US$ 4 registered.

**This is a null about the instrument, not about the judge.** The design needed problems the
production judge fails twice on which three weaker writers produce three distinct texts with at least
one right and one wrong. Fifty of the ninety AIME problems were asked before the collection was
stopped; **0** met the filter, and the two conditions that killed it are both measured below. No
pipeline run was made, so nothing here bears on the registered question — whether the judge's
verdict follows the vendor name or the position — and the prediction is neither confirmed nor
refuted.

## What fifty problems showed

| | n |
|---|---:|
| problems asked | 50 |
| judge alone (`deepseek-v4-flash-0731`, temperature 0.1, two attempts): right both times | **35** |
| right once | 8 |
| wrong both times — the problems the design needed | **7** |
| of those 7: writer slots that came back **empty** (of 21) | **13** |
| problems with three distinct non-empty texts, at least one right and one wrong | 2 (`aime-22`, `aime-23`) |
| of those 2: the judge wrong both times | 0 |
| **items kept** | **0** |

Per writer, over all 50 problems (one call each, temperature 0.3, 16,000-token completion budget):

| writer | empty | right | non-empty median / max chars |
|---|---:|---:|---|
| `z-ai/glm-5.3-flash` (reasoning) | **18 / 50** | 30 / 50 | 1,388 / 2,498 |
| `openai/gpt-oss-20b` (reasoning) | **13 / 50** | 35 / 50 | 1,286 / 2,634 |
| `moonshotai/kimi-k2` (not reasoning) | 2 / 50 | 35 / 50 | 3,229 / 39,317 |

## The two conditions, and why they compound

**The judge is at a ceiling on this corpus too.** 86% of problems solved on both attempts, 94% on at
least one. `bench/judge_blind` reached this conclusion on GSM8K (240 / 240 named, 119 / 120 blind:
the judge re-derives the arithmetic); AIME 2022–2024 moves the ceiling from ~99% to ~86%, and that
is not far enough. The registered filter (wrong on **both** attempts) leaves 14% of the set.

**On that 14% the writers run away.** The two reasoning writers return an empty `content` when the
reasoning spends the whole completion budget — the same failure `bench/spec_test_vacuity`'s probe
measured the same day on the generator (every empty reply `finish_reason=length`; one unbounded call
reasoned to the provider's 131,072-token ceiling and returned nothing). Here the hard problems are
exactly the ones that provoke it: 13 of the 21 writer slots on the seven judge-unsolved problems came
back empty, against 18 + 13 + 2 = 33 of 150 overall. The filter's *"three distinct texts"* clause
and its *"judge wrong twice"* clause select against each other, because both select for difficulty.

So the yield is not low; it is zero by construction, and the projection at ninety problems was zero
or one. The collection was stopped at fifty (amendment 1) rather than spending the remaining
≈ US$ 2 on forty more problems that would have refined a zero.

## Amendment

1. *(2026-09-11, after 50 of 90 problems, before any pipeline call.)* Collection stopped at 50. Reason
   above; the fifty rows are published in full so the count can be checked.

## What a workable instrument needs, priced

The registered question stands. Three routes, none taken here:

- **Writers at least as strong as the judge.** On problems the judge cannot solve, only stronger
  models produce the right text the design needs; the production panel under its own names
  (`claude-opus-5`, `gpt-5.5`, `gemini-3.8-flash`) at a 32k budget on the ~13 judge-unsolved
  problems of the full set is ≈ US$ 6–9 for the collection alone, and yields at most ~13 items —
  enough for direction, not for a 10 pp vendor gap (16 runs per name; Wilson ± 20 pp).
- **A judge that cannot re-derive.** The paper's bias lives where the judge cannot check. A
  closed-book factual corpus with exact-match references (SimpleQA, MIT) gives that: no arithmetic
  to redo, a writer either knows the fact or does not, and answers are short (cheap). This is the
  instrument the next attempt should build, and it needs a new pre-registration, because the
  filter changes (the judge-alone clause stays; the difficulty coupling above disappears).
- **A weaker judge.** Measures a bias in a judge nobody ships; not worth the money.

## What this cannot show

Anything about the judge's bias — the pipeline never ran. The judge-alone rate is a ceiling
statement about this instrument (AIME is in every model's training data), not a capability claim.
Three writers at one budget: the empty rate is the runaway rate at 16k, and a 64k budget would
lower it at a cost this file does not measure. Fifty of ninety problems.
