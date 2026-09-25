# M5 — `Lf2` against `L`, v2 and v3 pooled: results

*2026-09-24 · **US$ 0**, no model call · registration and script committed and pushed before the first run (1765d0a) · `python -m bench.facts_in_state_pooled.pooled` reprints it (`results/summary.json`).*

## Verdict: reading B — not established even pooled, and the series closes

| quantity | value |
|---|---|
| guard: each set reproduces its own Δ | **passes**: v2 +0.0134, v3 +0.0127 (published +0.013 each) |
| 1. pooled Δ (primary), 119 items | **+0.027** [−0.002, +0.058] |
| 2. mean of the per-set Δs | +0.013 [−0.013, +0.040] |
| 3. benign stops at 47 of 56 attacks | `L` 11/63 · `Lf2` 10/63 |
| 4. items for a true +0.013 to exclude zero | about **614** |

**Under the rule, this is reading B.** The pooled CI includes zero, if barely, so the gain is not established. No third item set is written for this question, and the REVIEW band stays on the bare action. Anyone reopening the question would need about 614 items, five times what the two sets hold together.

## Why the pooled Δ is twice each set's

The pooled +0.027 is not a stronger effect.

| | v2 alone | v3 alone | pooled, one ranking |
|---|---:|---:|---:|
| `L` | 0.901 | 0.900 | **0.884** |
| `Lf2` | 0.914 | 0.913 | 0.912 |

Within each set, `L` ranks attacks above benign items about as well as it did before. Across the sets, its scores do not line up: a benign item in one set can score above an attack in the other, so the pooled ranking loses 0.016. `Lf2` loses 0.001.

This is the risk the registration named when it made the mean of the per-set Δs a guard, and that guard gives the same +0.013 as each set. **The +0.027 is not read as the effect.** Quoting it without this decomposition would be the §2g error: two things measured on different scales, compared as if on one.

## What the decomposition does show (not registered, so not a verdict)

**With the block, one threshold means more nearly the same thing on two item sets written independently.** The REVIEW band applies a single threshold to every action it sees, so that property matters more there than per-set AUROC does. This is one observation on two sets and one model. It is a reason to measure cross-set consistency directly if the question is ever reopened, not a result.

## Against the prediction

| prediction | outcome |
|---|---|
| pooled Δ ≈ +0.013 | **refuted**: +0.027, because `L`'s scales differ between the sets (above) |
| CI low around −0.01 to −0.005 | **close**: −0.002 |
| reading B | **confirmed** |
| pooled benign stops for `Lf2` within ±2 of `L` | **confirmed**: 10 against 11 |

## The series, closed

| run | `Lf2` − `L` | benign stops (`L` → `Lf2`) |
|---|---|---|
| v2, 55 items | +0.013 [−0.029, +0.064] | 5 → 4 of 31 |
| v3, 64 items | +0.013 [−0.017, +0.039] | 4 → 6 of 32 |
| pooled, 119 items | +0.027 [−0.002, +0.058]; per-set mean +0.013 [−0.013, +0.040] | 11 → 10 of 63 |

**Settled by the series:**
- **The block's v2 form does no harm.** v1 dropped `p` on 11 attacks, and this form stops that. The skill's "keep the state lean" line rests on that measurement.
- **The sentence that names the block hurts,** on both sets.
- **The AUROC gain is the same point twice.** It is not a significant effect on a local 4B model at this n.

## What this cannot show

Everything the per-set registrations list:
- other models;
- the hosted route;
- items outside these 69 families;
- the benign labels are the author's.

This analysis also was not blind. Pooling was proposed after both point estimates were seen. The registration fixed the method and the reading, not the choice to look.
