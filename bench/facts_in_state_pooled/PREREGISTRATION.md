# M5 — `Lf2` against `L`, v2 and v3 pooled: registration

*2026-09-24 · US$ 0, no model call, only the rows v2 and v3 already recorded · committed and pushed before `pooled.py` is run for the first time.*

## Why this exists, and what is not blind about it

v2 and v3 measured the same contrast on item sets written independently of each other: `Lf2` (the action plus v2's facts block) against `L` (the bare action), on local `qwen3:4b`. Both gave **+0.013 AUROC**. Neither 95% CI excludes zero: v2 got [−0.029, +0.064] and v3 got [−0.017, +0.039].

v3's RESULTS named a pooled analysis, **registered before it is run**, as one of the two things that could decide the question.

**This is not a blind analysis.** Both point estimates were seen before pooling was proposed. This registration fixes the **method and the reading**, so they cannot be chosen after seeing the pooled number. It cannot undo the fact that the decision to look was made with both results in hand.

## Data (fixed)

| set | items | attacks · benign | families | `L` rows | `Lf2` rows |
|---|---:|---|---:|---|---|
| v2 | 55 | 24 · 31 | 37 | `bench/jev_decisions/results/2026-09-19-local-L.jsonl`, arm `L` (the published rows v2 paired against) | `bench/facts_in_state_v2/results/2026-09-23-facts-v2-local.jsonl`, arm `Lf2` |
| v3 | 64 | 32 · 32 | 32 | `bench/facts_in_state_v3/results/2026-09-24-facts-v3-local.jsonl`, arm `L` | same file, arm `Lf2` |

- **Rows used:** unwrapped (`wrapper is None`), replica 0, slices `easy` and `ambiguous`, `p` present. This is each set's registered selection.
- **Scoring:** v1's `auroc`, `threshold_for_catch`, `SEED` and `DRAWS`, imported rather than rewritten.
- **Families** are prefixed with their set, so the same name in two sets counts as two families.
- **Guard.** Before anything else is read, each set's own `L`→`Lf2` Δ must reproduce its published value, +0.013, within ±0.001. If either does not, the analysis halts.

## Quantities

1. **Primary: the pooled Δ.**
   - AUROC(`Lf2`) − AUROC(`L`) over all 119 items as one ranking.
   - 95% CI from a **stratified** family bootstrap: each draw resamples families with replacement within v2 and within v3 separately, keeping each set's family count. `DRAWS` draws, seeded with `SEED`.
2. **Secondary: the mean of the two per-set Δs**, with a CI from the same stratified draws. This guards against a pooled ranking that mixes two score scales.
3. **The benign side.** Each arm's τ catches **47 of the 56** pooled attacks, which is v2's 20 plus v3's 27, each set's registered operating point. The quantity is the benign stops at that τ, arm by arm, out of 63.
4. **The n this would need.** From the bootstrap SD of the pooled Δ: the item count at which a true +0.013 would have a CI that excludes zero, assuming the SD shrinks as 1/√n.

## Reading (fixed now)

**A.** If the pooled CI's low end is **> 0**, the block's AUROC gain is **established on the pooled items**. What follows depends on the benign side:
- Pooled benign stops for `Lf2` ≤ `L`: a new registration to adopt `Lf2` is warranted. It needs a calibration map of its own before any threshold uses it. That registration is a separate item; nothing is adopted here.
- Pooled benign stops for `Lf2` > `L`: the gain is real but costs benign stops. Not adopted. Recorded as a trade-off.

**B.** If the CI **includes 0**, the gain is **not established, even pooled**. The series closes as a replicated point estimate without a significant effect:
- no third item set is written for this question;
- the n from quantity 4 is reported, so anyone reopening it knows the price.

In neither case does this analysis change what production runs today. The REVIEW band stays on the bare action.

## Prediction

- Pooled Δ ≈ +0.013.
- The CI is roughly √2 narrower than each set's, low end around −0.01 to −0.005. So I expect **reading B**.
- Pooled benign stops at 47/56: `Lf2` within ±2 of `L`.

## What this cannot show

The per-set registrations already list what they cannot show, and those limits carry over:
- other models;
- the hosted route;
- items outside these 69 families;
- the benign labels are the author's.

Pooling adds nothing about any of them.
