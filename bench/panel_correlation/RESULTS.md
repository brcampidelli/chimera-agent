# Three panel models carry one and a half votes

**2026-09-13.** **US$0** — measured on per-member answers two earlier benches already left on disk.
The question was left open by `bench/design_effect` (#461), which located an un-clustered Wilson
bound in the evolution gate and refused to discount it with an ICC measured on a different
population. This is that population.

## The verdict in one line

On the corpus with real variance, our three-model panel agrees far beyond what independent members
would: **34 of 50 items unanimous where 16.5 were expected**, ICC(1) **+0.527** — so
**three members carry 1.46 independent votes, not three.**

## 1. Two corpora, and one of them cannot answer

| corpus | members | items | accuracies | usable? |
|---|---|---:|---|---|
| `fusion_aggregate` (arithmetic, frontier models) | opus-5, gpt-5.5, gemini-3.1-pro | 40 | 0.950 / 0.975 / 1.000 | **no — at ceiling** |
| `judge_blind_hard` (AIME, cheaper models) | kimi-k2, gpt-oss-20b, glm-5.3-flash | 50 | 0.700 / 0.700 / 0.600 | **yes** |

The frontier panel makes **three errors in 120 cells**. Its ICC computes to +0.326 and that number
is driven by two items; it is reported and then set aside (§2q — an instrument that cannot exhibit
the effect produces no evidence about it). Note also that `bench/fusion_aggregate/results/` is
gitignored, so that half is reproducible only where the run happened. The corpus that decides is
tracked.

## 2. The panel is not three voters

`judge_blind_hard`, 50 AIME items, against the exact Poisson-binomial null for members with those
three accuracies:

| members correct | observed | if independent |
|---:|---:|---:|
| 0 | **9** | 1.8 |
| 1 | 7 | 11.1 |
| 2 | 9 | 22.4 |
| 3 | **25** | 14.7 |

**Unanimous (all right or all wrong): 34 observed against 16.5 expected.** The two tails are where
it lives — five times as many items that every member got wrong, and nearly twice as many that every
member got right. The middle, where independent voters would spend most of their time, is half as
populated as it should be.

ICC(1) over items **+0.527**, so at a panel of three the design effect is 2.05 and the panel carries
**1.46 independent votes**.

That is arXiv 2609.10969's finding arriving in our own numbers. Their measurement was that voting
over *shared* evidence approves 62.9% of unsafe proposals against 22.9% with independent sources — a
**40.9 pp source effect against an 11.3 pp model effect**. Our panel varies the model and shares the
question, and 1.46-of-3 is what that costs.

## 3. What it means for the gate that reads "3 of 3"

`chimera/evolution/auto_evolve.py` accepts a learned skill on `transfer_counts`: how many of the
panel's models ran it and produced output. Both modes read that count as independent evidence —
`point` divides it (`passed / n`) and `wilson` puts a confidence bound on it. **Neither is entitled
to.** A unanimous panel is the single most likely outcome under correlation and the *least*
informative one: when 34 of 50 items are unanimous, "all three agreed" is close to the base rate.

Two things changed, and a third deliberately did not:

- **`wilson_lower_best_of` now takes an explicit `effective_n`.** It defaults to `None`, which
  behaves exactly as before, and its docstring now says out loud that `n` is a count of trials and
  is only the *information* in those trials when they are independent. The assumption was invisible;
  now it has a parameter.
- **The gate warns once** that the transfer panel's votes share an input, citing this measurement.
- **No discount is applied.** The 0.527 here is three specific models on AIME; the gate's panel is
  different models running a learned skill against `bool(out.strip())`, which is a far weaker check
  and could be more correlated or less. Applying this number there would be importing from the wrong
  population — the exact error #461 refused to make, and refusing it twice is the point.

The default `accept_mode` is `point`, so the Wilson path is opt-in and most installs never take it.
That does not make the reading safe: `point` states `3/3 = 1.00` where the honest denominator is
nearer 1.5, and a fraction with an inflated denominator is the same error without an interval
around it.

## 4. What this cannot show (§2q)

- **Three models, one domain, 50 items.** AIME is a domain where errors are strongly item-driven —
  a hard problem is hard for everyone — which is exactly the mechanism under test, and also exactly
  why the number may not carry to a domain where difficulty is flatter.
- **It is not the gate's own panel.** Transferring 0.527 to `auto_evolve` is the thing §3 refuses.
  Measuring the gate's panel needs the gate's corpus and a paid run nobody has funded.
- ICC(1) with three members is a small statistic; the tail counts (9 against 1.8) carry the finding
  more robustly than the coefficient does, which is why both are reported.
- The frontier-panel half is at ceiling and is evidence about nothing.

## 5. Reproducing

```bash
python bench/panel_correlation/measure.py    # under a second, no spend
```

`judge_blind_hard/results/collect-all.jsonl` is tracked, so the deciding half runs from a clean
clone. The ceiling half runs only where `bench/fusion_aggregate/results/` exists.
