# Pre-registration — does the fusion judge's verdict follow the model name and the position it is shown, rather than the answer?

**Registered 2026-09-11 against `2fcf4bc` (v0.53.0 + #421–#424), before a single model call.**

## Where the question comes from

`FusionEngine._run_judge` (`chimera/fusion/engine.py:502-519`) hands the judge the panel's answers
as `--- Answer 1 (model openrouter/anthropic/claude-opus-5) ---`, in the order the panel returned
them. `_run_synth_agreed` (line 532) does the same for the synthesiser on the agreed path. The judge
therefore knows **which vendor wrote each answer and which answer came first** — two things that are
not evidence about the answer.

arXiv:2609.08016 (slice 08 of the 2026-09-11 sweep) measures both as biases in LLM judges: a
preference for the first candidate and for the candidate labelled with the larger or better-known
vendor, most of it removed by blinding the labels and shuffling the order. The project's own
`role_kinship` (line 182) already treats the judge's *lineage* as something to keep independent; it
has never asked whether the judge's *reading* is independent of the names on the page.

Blinding costs nothing — letters instead of slugs, a permutation kept in the trace so the receipt
still attributes every answer — so the question is not "is it worth it" but "does the judge use the
names for anything, and is any of it useful". If the names carry a productive prior (the judge
rightly trusts the stronger vendor more often than not), blinding would cost accuracy; the paper says
the prior is a bias, not a signal. That is what this measures.

## What is held fixed, and what is real

The **judge and synthesiser are the production ones**: `_DEFAULT_JUDGE`
(`openrouter/deepseek/deepseek-v4-flash-0731`, temperature 0.1) and `_DEFAULT_SYNTHESIZER`
(`openrouter/anthropic/claude-opus-5`, temperature 0.3), called through the production
`FusionEngine._aggregate(messages, panel)` — the same code path every fused turn takes from a panel
to a final answer. Nothing in the pipeline is mocked.

The **panel answers are real model outputs, but not from the production panel.** The production trio
(Opus 5 / GPT-5.5 / Gemini 3.8 Flash) is at ceiling on any gradable corpus we have
(`bench/fusion_paired/RESULTS.md`: 25/25 single-model solves), and a judge only matters when the
panel disagrees. The answers are therefore produced by three small models from three vendors —
`openrouter/meta-llama/llama-3.2-3b-instruct`, `openrouter/google/gemma-3-4b-it`,
`openrouter/meta-llama/llama-3.1-8b-instruct` — on GSM8K, and only questions on which **at least one
answer is right and at least one is wrong** are kept.

*Amendment, same day, before any judge call:* the first draft named the weak tier itself
(mistral-small 24B, llama-3.3 70B, gpt-oss 20B) as the writers. Collected live, they agreed on
**51 of 52** questions — a disagreement rate of ~2%, which would have yielded about eight items from
the 400-question cap. An instrument that cannot exhibit a disagreement cannot measure a judge, so
the writers moved down to 3B–8B models; nothing else in the design changed, and the names the judge
is shown are still the production panel's. The judge is then shown those answers **under the
production panel's names**, rotated: the same mistral text is labelled `claude-opus-5` in one run
and `gemini-3.8-flash` in another. That is the manipulation — the text never changes, only the name
and the position — and it is the only way to attribute an effect to the label rather than to the
answer.

**Corpus.** GSM8K test split, fetched by `bench/llm_benchmarks/datasets.py` from its canonical URL
(not vendored), graded by that bench's `extract_answer` / `grade` (exact numeric match on the final
`ANSWER:` line or the last number). Questions are sampled with seed 7 until **40** disagreement items
are collected, or 400 questions have been asked, whichever comes first; the composition (1 right /
2 wrong vs 2 right / 1 wrong; whether the wrong answers agree with each other) is reported.

## Conditions — the same three answer texts, eight pipeline runs per item

| arm | labels shown to the judge | order | runs per item |
|---|---|---|---|
| `named` | the production slugs, assigned by cyclic rotation r ∈ {0, 1, 2} | original, reversed | 6 |
| `blind` | `Answer A / B / C` (the change under test, `FusionConfig.blind_panel=True`) | original, reversed, seeded shuffle | 3 |

Every run is judge → synthesiser through `_aggregate`; the outcome is whether the synthesised final
answer is correct. The `task_typed` vote path stays off (the default), so the judge is always in the
loop — a majority vote would bypass exactly the stage under test.

## Outcomes, scored deterministically

- **Primary — blind vs named:** per item, the mean accuracy of the 6 named runs and of the 3 blind
  runs; the paired difference over items with a Newcombe interval on the per-item majority verdicts
  (`compare_paired`).
- **Vendor effect:** among named runs, accuracy conditional on **which slug was attached to a
  correct answer** (three groups of 80 runs). The spread max − min, with Wilson intervals; the
  paper predicts the "largest" name wins.
- **Position effect:** accuracy conditional on the position of a correct answer (1, 2, 3), from the
  named runs and separately from the blind runs. The paper predicts position 1 wins in the named
  arm; blinding does not remove a position effect on its own — shuffling does, and that is why the
  blind arm shuffles.
- **Signature of the mechanism:** the fraction of judge analyses in named runs whose text names a
  vendor or model (`opus|gpt|gemini|anthropic|openai|google`) — the judge reasoning *about the
  label*. Ten such analyses printed in `RESULTS.md`, read by hand.
- Tokens and USD per arm.

## Decision rule, written before the numbers

- **Adopt `blind_panel=True` as the default** if the paired difference blind − named has a 95%
  interval whose lower bound is ≥ −5 pp. Blinding is free and the paper's prior is a bias; the only
  reason to keep names is a measured loss, and this is the size of loss that would count.
- If blind is worse by more than that: keep the names, publish the number, and record that this
  judge uses vendor identity productively on this corpus.
- The vendor and position effects are reported with intervals **whatever the primary says**; a
  spread of ≥ 10 pp between names, with intervals that do not overlap, is reported as bias in the
  shipped configuration.

## Cost

Panel collection: ≤ 400 questions × 3 weak models ≈ 1,200 short calls, ≈ US$ 0.5. Pipeline: 40 items
× 9 runs × (judge + synthesiser) = 720 calls, of which 360 are the synthesiser at Opus 5 rates —
≈ US$ 4–6. Total **≈ US$ 5–7**.

## What this cannot show

GSM8K answers are short and gradable; a fused *code* or *prose* turn is neither, and a judge's
label bias may be larger where nothing is checkable. The panel texts come from weak models, so the
judge is grading weaker prose than the production panel writes — the *names* are production, the
*writing* is not. One judge, one synthesiser, one temperature each. It measures the judge → synth
stage in isolation from the panel stage, which is the stage the change touches and the only one
that can be measured without a corpus the production panel does not saturate.
