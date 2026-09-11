# Results — the judge re-derived every answer, so the names had nothing to act on; blinding cost nothing and is now the default

Run 2026-09-11 · 40 GSM8K disagreement items × 9 pipeline runs = 360 runs through the production
`FusionEngine._aggregate` (judge `deepseek-v4-flash-0731`, synthesiser `claude-opus-5`) · US$ 4.32
· prereg `PREREGISTRATION.md` (two dated amendments, both before the first judge call) · raw
`results/2026-09-11-runs.jsonl`, `results/items.jsonl`, report `results/2026-09-11-report.md`.

## The number

| arm | runs | correct final answer |
|---|---:|---:|
| `named` — production slugs, rotated over the same texts, three orders | 240 | **240/240** |
| `blind` — `Answer A / B / C`, the engine's own shuffle | 120 | **119/120** |

The one miss is a unit: the synthesiser answered *3.75 hours* where the reference is *225
minutes* — the same quantity, graded by exact numeric match. Per-item majority, named → blind:
1.00 → 1.00, Δ 0, discordant 0.

**Vendor effect: none measurable.** Accuracy when a correct answer carried `claude-opus-5`,
`gpt-5.5` or `gemini-3.8-flash`: 1.00, 1.00, 1.00. **Position effect: none measurable** — 1.00 at
positions 1, 2 and 3, in both arms. **The signature never appeared:** in 0 of 240 named runs did
the judge's analysis mention a vendor or model name.

## Why the instrument could not exhibit the effect (§2q)

The paper's judge picks between candidates it cannot solve. This judge is a reasoning model, and
in **169 of 240** named analyses it states the answer itself — it re-derives the arithmetic, marks
the candidates against its own result, and the synthesiser follows the analysis. On GSM8K a
disagreement among 3B–8B writers is a problem the judge solves alone; the names on the answers
are not consulted because nothing needs them. That is the ceiling, and it is a fact about the
pairing (a reasoning judge, a corpus it can do) rather than about label bias in general. The
pre-registration did not carry a ceiling rule for the *judge*, only for the writers; it should
have, and this file is where that is said.

## The decision, as registered

The rule was written for the case where blinding costs something: *adopt if the paired
difference has a lower bound ≥ −5 pp*. It holds at Δ = 0 with no cost anywhere in the table — and
that is the whole of the evidence: **blinding is free on this corpus**, the paper's prior stands
unrefuted, and a label the judge does not need is a label it should not be shown. `FusionConfig.
blind_panel` and `CHIMERA_FUSION_BLIND_PANEL` default to **on**; the trace keeps `shown_order`, so
the receipt still attributes every letter to its model. What is *not* claimed: that this judge was
biased by names — this run could not have shown it either way.

## What would show it

A corpus the judge **cannot** solve alone, so that it has to choose: hard reasoning or code where
the production judge's own single-call accuracy is under 50%, with the same name-rotation design.
Filtering GSM8K by "the judge alone gets it wrong" selects for the judge's weaknesses and would
give a handful of items; a harder set is the right instrument, and it is the follow-up this file
names.

## What this cannot show

GSM8K only; one judge, one synthesiser; the writers were 3B–8B models under production names, so
the *prose* was weaker than the production panel's. Nothing here bears on fused code or prose
turns, where nothing is checkable and a label may weigh more.
