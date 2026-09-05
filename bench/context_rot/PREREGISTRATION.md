# At what context length does the model stop doing what it was told? — pre-registration

**Written before any measurement. No outcome was seen first.**

`bench/compaction/RESULTS.md` ended by naming this as the measurement the project needs and does not
have. The compaction trigger is a fraction of the model's **advertised** window; the shipped default
advertises 1,310,000 tokens, so the Code screen compacts at 786,000, and nothing in 137 traced runs
ever came within a factor of twelve of that. When windows were 8k and 128k, "0.6 of the window" and
"as much as the model can still use well" were roughly the same number. They are not now, and this
measures the second one.

## Why the bench that already exists cannot answer it

`chimera/eval/context_curve.py` measures the same phenomenon **observationally**, joining
`runs.jsonl` to `traces.jsonl` and bucketing by peak context. Run on this install's logs it reports:

```
0–8000        47 attempts   42.6%  [29.5%, 56.7%]
8000–32000    35 attempts   62.9%  [46.3%, 76.8%]
32000–128000  15 attempts   — (below the pre-registered floor of 20)
128000–∞       0 attempts   —
not enough data: 2 buckets above the floor, need 3. No claim either way.
```

It is right to refuse, and it would be right to refuse even with ten times the data, because the
confound is structural: **a harder task carries more context**, so success falling with length would
be indistinguishable from length rising with difficulty. Note that in this sample success *rises*
with context — which is that confound, not an absence of rot.

Attribution needs the task held constant and only the length varied. That is what this does.

## Design

A **single model call**, not an agent run. The loop adds attempts, tool calls and retries, and each
of those is a source of variance that has nothing to do with context length. Isolating the model is
what makes the answer a fact about the model rather than about the harness around it.

One prompt, assembled the same way at every length:

1. **System** — the shipped default system prompt, unchanged.
2. **The brief**, first user turn: three checkable rules for the output, plus one arbitrary fact
   (a token) stated once and never repeated.
3. **Padding**, N tokens of plausible prior conversation — real source files from this repository,
   framed as earlier turns. Real text rather than repeated filler, because a repeated string is
   compressible and a model may treat it differently from novel content.
4. **The request**, last user turn: write one small function, and quote the fact back.

## Two outcomes, measured separately

They come apart, and reporting one as the other is the failure this design is built to avoid:

- **RULE** — does the output obey the three rules given in the brief? Instruction-following across
  distance.
- **FACT** — is the arbitrary token reproduced exactly? Retrieval across distance.

The literature's claim is that retrieval survives far longer than instruction-following. If that
holds here, a run that measured only FACT would report a model working fine at a length where it has
stopped obeying — which is precisely the mistake a needle-in-a-haystack test makes when it is used to
justify a context budget.

## Lengths

**4k · 16k · 64k · 200k · 400k · 786k** prompt tokens, 10 repeats each, per model.

786,000 is not a round number. It is the exact point at which the Code screen's `context_budget=0.6`
would finally compact on the shipped default, and it is in the sweep so that the question "is the
model already broken by the time Chimera acts?" has a direct answer rather than an extrapolated one.

## Models

- `openrouter/deepseek/deepseek-v4-flash-0731` — the shipped default, 1,310,000-token window
- `openrouter/z-ai/glm-5.3-flash` — a second family, so a curve is not one vendor's artefact

## What is reported, and the rule for reading it

Per (model, length): RULE pass rate and FACT pass rate over 10 repeats, each with a Wilson interval.

**The knee is where the RULE rate first drops below 80% of its 4k value, with the interval
excluding that 4k value.** Reported as a length, or as "no knee below 786k" if none is found.

No adopt/reject: this run produces a number, not a decision. What it feeds is a later decision about
the shape of `context_budget`, and pre-committing that decision now — before knowing whether the
knee is at 30k or at 600k — would be fixing an answer to a question with no data.

## Gates before the numbers are believed

- **The 4k point is the control.** If RULE is already below 90% at 4k, the task is too hard and the
  curve measures the task rather than the length. Reported first.
- **Actual prompt tokens are read from the provider's usage**, not from the padding estimate. A
  target of 786k that arrives as 400k is a different measurement than the one this document
  describes.
- **Three completions at the longest length are read with human eyes.** A model that returns an
  error string, a refusal or a truncation scores zero on both outcomes and would look like rot.

## What this cannot show

- **Two models, one tier.** Both are flash-class. A frontier model in either family may hold
  instructions much further, and nothing here transfers to it.
- **One task shape.** Three formatting rules and a token to echo. A model that loses arithmetic, or
  loses the ability to plan, at a length where it still formats correctly would look fine here.
- **A single call, not a conversation.** Real degradation in an agent loop compounds over turns; this
  measures one pass. `harness-engineering.md` calls those different things — rot within a pass, and
  drift across a trajectory — and this measures only the first.
- **Padding is repository source.** Plausible for a coding agent and not neutral: code may hold
  attention differently from prose, and a different padding could move the knee.

## Cost

Roughly 120 calls, most of them short. Estimated **under two dollars**, reported as measured.

## Result

`bench/context_rot/RESULTS.md`.
