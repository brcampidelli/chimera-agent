# Results — on facts the judge cannot recall, its verdict followed neither the vendor name nor the position; the blind panel scored higher once, and not again with the judge bounded

Run 2026-09-12 · prereg `PREREGISTRATION.md` (one registered addendum, below) · corpus
`results/items.jsonl` (38 items) and `results/collect-all.jsonl` (all 400 questions asked) · runs
`results/2026-09-12-runs.jsonl` (341 of 342, the judge unbounded) and
`results/2026-09-12-runs-capped.jsonl` (342 of 342, the judge and synthesiser bounded — the
addendum) · US$ 0.16 collection + US$ 0.63 + US$ 0.09 pipeline.

## The corpus, and the instrument check it is

SimpleQA (MIT), a seeded sample of 400 questions with references of at most five tokens. The
production judge (`deepseek-v4-flash-0731`, temperature 0.1), asked twice closed-book, was wrong
**both** times on **298 of 400** — the property `bench/judge_blind` (GSM8K, 240 / 240 named) and
`bench/judge_blind_hard` (AIME, 35 / 50 both attempts) could not reach. Three non-reasoning writers
from three vendors: `kimi-k2` right on 79 / 400, `llama-3.3-70b` 49 / 400, `gemma-3-27b` 32 / 400.
No writer returned an empty text — the runaway that emptied 13 of 21 slots on AIME is a
reasoning-model failure, and these are not reasoning models. Kept: **38** items where the judge
failed twice and the writers disagreed (29 with one right text, 9 with two); excluded: 253 where no
writer was right, 102 where the judge was right at least once, 6 with empty or duplicate texts, 1
where all three were right. On the 29 one-right items the two wrong writers never gave the same
answer, so a majority vote is never available to the judge there.

## The numbers

| arm | runs | passed | Wilson 95% | judge names a vendor | mean tokens / run |
|---|---:|---:|---|---:|---:|
| `named` (the production names, balanced rotation) | 227 | **141 / 227** (0.62) | [0.56, 0.68] | **1 / 227** | 11,270 |
| `blind` (`blind_panel=True`, the default) | 114 | **83 / 114** (0.73) | [0.64, 0.80] | 0 / 114 | 9,675 |

**Vendor effect (named runs):** accuracy when the correct text carried `claude-opus-5` **0.67**
[0.57, 0.76] (94 runs); `gpt-5.5` **0.69** [0.59, 0.78] (94); `gemini-3.8-flash` **0.62**
[0.52, 0.72] (93). Largest gap 7 pp, every interval overlapping every other.

**Position effect (named runs):** correct text at position 1 **0.67**, 2 **0.65**, 3 **0.67**.
Blind runs: 0.81 / 0.74 / 0.73, intervals overlapping (42–54 runs each).

**Named against blind.** Per-run, 0.62 against 0.73. Per item (38, both arms present): mean accuracy
0.622 against 0.728, difference **+0.106**, bootstrap 95% [+0.013, +0.199]; the blind arm scored
higher on 17 items, lower on 7, equal on 14. The report's registered per-item-majority pairing
prints Δ +0.18 [+0.03, +0.23]; that number is inflated by a rule the pre-registration did not think
about — the named arm has six runs per item and four items sit at exactly 3 / 6, which the majority
rule counts as a fail — and the per-item means above are the comparison to read.

**One halt.** `sqa-51 named r2 o0` was still inside the judge after forty minutes when the run was
stopped (the reasoning judge's runaway, `bench/PROTOCOL.md` rule 2); it is absent from the file,
not a failure. Three finished runs took 1,655–3,096 s for the same reason.

## Against the registered predictions

- *Accuracy when the correct text carries `claude-opus-5` exceeds `gemini-3.8-flash` by ≥ 10 pp* —
  **failed**: +5 pp, intervals overlapping. The judge named a vendor in **1 of 227** analyses, and
  that one is a consensus sentence (*two of the three candidates, Claude Opus and GPT-5.5, agree*).
- *Position 1 beats position 3 by ≥ 5 pp in the named arm* — **failed**: 0.67 against 0.67.
- *The blind arm shows no vendor gap and a position gap no larger than the named arm's* — the first
  holds by construction; the second **failed** in direction (8 pp between positions 1 and 3 in the
  blind arm against 0 in the named), inside overlapping intervals.
- *Blind accuracy within ±5 pp of the named mean* — **failed**: +11 pp per run, +10.6 pp per item.

## What ships, by the rule written before the numbers

The rule: a vendor gap ≥ 10 pp with non-overlapping intervals is published as bias in the shipped
configuration; anything smaller is a null with the intervals; blind worse than named by > 5 pp
revisits the default. The gap is 7 pp at most with every interval overlapping: **a null**, on the
one corpus built so far where the judge could not solve the task itself. `blind_panel` stays the
default, as it was; nothing in code changes.

**What was not registered and is read as such.** The blind arm scored higher than the named one by
about 11 pp, with a per-item interval that excludes zero by one point. The pre-registration
predicted the two within ±5 pp and did not register a direction, so this is an observation, not a
result: one judge, one corpus, an interval that a second sample of 38 items could close. The judge's
analyses give no mechanism to hang it on — they mention a vendor once in 227 and argue from *known
biography* in both arms; the same item gets a different answer from run to run (*KSAN* / *KDIA*,
*Pittsford* / *Waterville*), which is a judge guessing between candidates it cannot verify. If the
difference is real, the reading arXiv 2609.08016 would give it is that names change what the judge
deliberates about — the named runs cost 16% more tokens — without changing which name it favours.
That is a hypothesis for the next corpus, with the direction now registered in advance.

## What this cannot show

Closed-book trivia, one judge, 38 items; the writers are weaker than the production panel whose
names they carry; a strict deterministic grader (every token of the reference on the `ANSWER:`
line) misgrades some right answers as wrong on both sides. Nothing here bears on a prose turn where
nothing is checkable. The judge *recognised* facts it could not *recall* — 0.62–0.73 against 0 / 2
alone — and that recognition, right or wrong, is what its verdicts are made of here.

## Addendum — the same 38 items with the judge and synthesiser bounded (registered, run the same day)

`FusionConfig.judge_max_tokens` and `synth_max_tokens` at 16,000, one retry on an empty reply
(twice the budget after `length`), and `Settings.completion_ceiling` at 32,000 for every call whose
caller set no budget. The same 38 items, the same nine runs each, the same judge and synthesiser.

| | unbounded (first run) | bounded (this run) |
|---|---:|---:|
| runs | 341 of 342 (one halt at 40 min) | **342 of 342** |
| seconds per run, median / p95 / max | 45 / 1,000 / 3,096 | **30 / 266 / 1,187** |
| completion tokens per run, median / p95 / max | 1,901 / 48,371 / 145,999 | 1,431 / 9,711 / 33,419 |
| US$ per run (total) | 0.00184 (0.63) | **0.00025 (0.09)** |
| share of cost in the top 5% of runs | 62% | 28% |
| runs whose judge analysis came back empty | 14 | **2** |
| retried stages (empty first reply) | — | 31 of 684 calls |
| `named` accuracy | 141 / 227 (0.62) | **162 / 228 (0.71)** |
| `blind` accuracy | 83 / 114 (0.73) | 80 / 114 (0.70) |

Paired per item, bounded against unbounded: `named` **+8.9 pp**, bootstrap 95% [+1.5, +16.2];
`blind` −2.6 pp, [−11.4, +6.1]. The 19 named runaways of the first run had passed 3 of 19 and the
10 blind ones 5 of 10; bounded and asked again, those runs pass like the rest.

**Against the registered predictions.** *p95 under 300 s* — held (266). *Maximum under 600 s* —
**failed** (1,187: one retry pair at 16k + 32k on a slow route). *Cost per run down ≥ 40%* — held,
by more: **−86%**. *Accuracy within ±5 pp in both arms* — held for `blind` (−2.6), **failed
upward** for `named` (+8.9): the cap did not cost accuracy, it returned some. *Retried stages under
5% of runs* — **failed**: 31 retries over 342 runs is 9%.

**The rule that failed, and why it is not followed.** The registration said that a retry rate over
5% means the budget is too low for this judge, and that both budgets should move to 32,000 and the
measurement be repeated. The unbounded run says otherwise about the premise: at the provider's
own ceiling of 131,072 tokens, **14 of 341** judge analyses still came back empty — a budget eight
times larger than 16k did not make those replies converge, because they are runaways of the draw,
not replies cut short. Doubling the budget would double what each runaway costs and recover nothing
the retry does not already recover (29 of 31 retries came back with text; 2 of 342 runs kept an
empty analysis, against 14 of 341 unbounded). So the budgets stay at 16,000 and the retry stays as
measured. This is a deviation from the rule as written, stated as one, with the number that decides
it — a second measurement at 32k would cost US$ 0.10 and is not made because its result is in the
first run's rows.

**What this changes about the section above.** The blind arm's 11 pp advantage did not survive
the cap on the same items: bounded, `blind` − `named` per item is **−0.9 pp**, [−7.9, +6.1], 11
items each way. Part of the first gap was the runaways (19 named against 10 blind, passing 3 and 5),
and the rest was one run's draw. Vendor effect, bounded: correct text under `claude-opus-5` 0.74,
`gpt-5.5` 0.79, `gemini-3.8-flash` 0.73 — a 6 pp spread; position 0.77 / 0.76 / 0.74. The null on
the registered question stands on both runs; the unregistered observation is read, on the same
items, as not replicated.
