# Results: arm V′, the hosted decision prompt with one output instruction

2026-09-25. Study 25, defect 5; plan §7 S9, candidate arm (b).
- **Registration:** `PREREGISTRATION-one-schema.md`, committed (`9ed59c84`) before any paid call.
- **Model:** `openrouter/deepseek/deepseek-v4-flash-0731`, pinned to DeepInfra. Every one of the
  222 attempts was answered there.
- **Cost:** US$ 0.035 at the conservative meter, plus the smoke (two calls, under US$ 0.001).
- **Rows:** `results/2026-09-25-one-schema.jsonl`. The report `run_one_schema.py --report` printed
  over it is in `results/2026-09-25-one-schema.summary.json`.

## Decision under the frozen rule: **V′ is adopted**, as a non-inferior simplification

| condition | result | |
|---|---|---|
| gate: V reproduces (pooled AUROC ≥ 0.85) | 0.973 | pass |
| gate: halts ≤ 10% per arm | 0 / 110 in each | pass |
| gate: every attempt answered by DeepInfra | 222 / 222 | pass |
| gate: no `thinking` argument on any call | none sent | pass |
| **1 · unparsed answers, paired: V′ ≤ V** | 0 against 0 | pass |
| **2 · ΔAUROC 95% lower bound > −0.05** | **−0.015 [−0.046, +0.009]** | pass |
| G1 · ΔBrier ≤ +0.02 | +0.007 [−0.013, +0.026] | pass |
| G2 · V′ catch at 0.5 ≥ V's − 3 | 42 against 41 of 48 | pass |
| G3 · V′ cost per call ≤ 1.2 × V's | US$ 0.000158 against 0.000163 | pass |

**Read it as "removing the sentence costs nothing this n can detect", not as "V′ is better".**
- Condition 1 passed as a tie at zero, and §6 of the registration said it would. The metric could
  not have shown a difference in this direction.
- Condition 2 passed by 0.004. The point estimate is slightly negative (−0.015). It is smaller than
  V′'s own gap between its two replicas (0.023, §3).

## 1 · Answers without a probability (per call, after the one re-ask)

| arm | calls | unparsed | empty | re-asked | cut at `length` | completion tokens p50 / p95 |
|---|---:|---|---:|---:|---:|---|
| V | 110 | **0 / 110** [0.00, 0.03] | 0 | 1 | 1 | 302 / 620 |
| V′ | 110 | **0 / 110** [0.00, 0.03] | 0 | 1 | 1 | 277 / 757 |

- **Every one of the 220 final answers starts with the JSON object.** The one-word instruction in V
  never produced a bare word, a prose answer or a malformed object.
- The single re-ask in each arm was the model reasoning through the whole 2,000-token budget on
  the first attempt (`git_reset-benign` in V, `find_delete-benign` in V′). The second attempt
  answered in both.
- So on this model, at its default reasoning, the contradiction is a defect of the text and not a
  failure the reader sees.

## 2 · Discrimination, calibration, operating point (both replicas, per call)

| arm | slice | n | AUROC | Brier | ECE (floor p95) | catch@0.5 | FR@0.5 | verdict catch | verdict FR |
|---|---|---:|---:|---:|---|---|---|---|---|
| V | easy | 40 | 1.000 | 0.007 | 0.030 (0.085) | 20/20 | 0/20 | 20/20 | 0/20 |
| V | ambiguous | 70 | 0.939 | 0.108 | 0.081 (0.123) | 21/28 | 5/42 | 25/28 | 6/42 |
| V | **pooled** | 110 | **0.973** | **0.072** | **0.059** (0.090) | 41/48 | 5/62 | 45/48 | 6/62 |
| V′ | easy | 40 | 1.000 | 0.008 | 0.027 (0.073) | 20/20 | 1/20 | 20/20 | 1/20 |
| V′ | ambiguous | 70 | 0.915 | 0.118 | 0.065 (0.101) | 22/28 | 5/42 | 23/28 | 6/42 |
| V′ | **pooled** | 110 | **0.958** | **0.078** | **0.037** (0.070) | 42/48 | 6/62 | 43/48 | 7/62 |

Reliability, pooled (p̄ → accuracy, five equal-mass bins):
- **V:** 0.03→0.00 · 0.10→0.00 · 0.24→0.36 · 0.78→0.82 · 0.98→1.00.
- **V′:** 0.01→0.00 · 0.06→0.05 · 0.22→0.27 · 0.76→0.86 · 1.00→1.00.

Both ECEs sit inside their simulated floors. The item-cluster bootstrap (10,000 draws, stratified
by label) gives:
- ΔAUROC = **−0.015 [−0.046, +0.009]**;
- ΔBrier = +0.007 [−0.013, +0.026].

Two things to read beside the rule:
- **The ambiguous slice carries the whole AUROC difference** (0.939 → 0.915). The easy slice is
  1.000 in both arms.
- **The verdict word moved 2 attack calls toward ALLOW** (45 → 43 of 48). The probability at 0.5
  moved the other way (41 → 42). This verdict was not a registered metric, and 12 of 110 (item,
  replica) pairs changed verdict between the arms, in both directions. It is listed so a later run
  can look for it, not as a finding.

## 3 · Replay floor, this session

| arm | AUROC replica 0 / 1 | median \|Δp\| | flips at 0.5 | verdict agreement |
|---|---|---:|---:|---|
| V | 0.972 / 0.976 | 0.050 | 2/55 | 47/55 |
| V′ | 0.947 / 0.970 | 0.040 | 4/55 | 49/55 |

The difference between the arms (−0.015) is smaller than V′'s own replica-to-replica gap (0.023).
Verdict agreement across the arms, 98 of 110 pairs, is within the arms' own agreement (47 and 49 of
55).

## 4 · Tokens and cost

- Paired per item, the mean completion tokens differ by a median of +1 token (V′ more on 28 items,
  fewer on 27; exact sign test p = 1). Removing the contradiction did not shorten the reasoning.
- V′'s prompt is 15 tokens shorter.
- DeepInfra reported prompt tokens that jump by about 79 between byte-identical requests (for
  example 169 and 248 for the same item and arm), in both arms. The token count is the provider's
  accounting, not the text; the price follows it.
- Cache reads: 0 in both arms.

## 5 · Against the predictions

| | predicted | measured | |
|---|---|---|---|
| P1 | unparsed 0–2 per arm, \|Δ\| ≤ 2 | 0 and 0 | met |
| P2 | re-asks ≤ 5% per arm | 1/110 each | met |
| P3 | ΔAUROC in [−0.03, +0.03], lower bound > −0.05 | −0.015, lower bound −0.046 | met, narrowly |
| P4 | V′ spends fewer completion tokens | no difference (28 against 27) | **not supported** |
| P5 | \|ΔBrier\| ≤ 0.02; catch at 0.5 within ±3 | +0.007; +1 | met |
| P6 | V pooled AUROC 0.90–0.98 | 0.973 | met |

## 6 · What this arm cannot show

Everything in §11 of the registration holds. In particular:
- **A small loss.** An AUROC loss smaller than about 0.05 on the ambiguous regime is not excluded.
  The point estimate there is −0.024.
- **Other framings.** The adoption covers the governance question on one model, pinned to one
  provider, at default reasoning. A reasoning-off call is still an unmeasured instrument.
- **Pinning against the date.** V pinned today ranks higher than V unpinned in September (0.973
  against 0.955 on the same two-replica count). The two runs differ in route and in date, and this
  run cannot say which of the two explains the gap.
- **The local backend.** It keeps the one-word line in its system text and asks for JSON under a
  schema. Its map is fitted on that text, so it is left as it is and was not measured here.
