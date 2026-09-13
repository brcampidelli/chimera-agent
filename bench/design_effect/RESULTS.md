# The correlation is worse than the paper's, and it prices replicas rather than correcting intervals

**2026-09-13.** Study 18 shortlist item #5. **US$0** — it reads the Harness-Bench factorial (#453),
whose 23 tasks × 8 arms × 3 replicas is exactly the grid ICC(1) wants.

## The claim being tested was ours

`bench/PLAN-study18-arxiv-sweep.md` §2, written before any of this code was read:

> **Our Wilson intervals over k runs of the same item are inflated.** `2609.06386` measures
> intra-group correlation at **0.530**, i.e. effective n = 1.70 for a group of 8 — a design effect
> of ~4.7×. **Anywhere we compute a CI over k outputs that passed the same gate (pass^k, replicated
> arms), the margin is narrower than the arithmetic says.**

That is a falsifiable statement about our own tree. This is its test, and it comes out in two
halves: the mechanism is real and **worse** than the paper's, and the exposure it was said to create
**is not there**.

## 1. Our correlation, measured — not imported

Using the shipped `chimera.eval.replicated.icc1`, one grid per factorial arm:

| arm | ICC(1) | design effect at k=3 | 69 runs behave like |
|---|---:|---:|---:|
| arm-000 | +0.5277 | 2.055 | 33.6 |
| arm-001 | +0.6031 | 2.206 | 31.3 |
| arm-010 | +0.7022 | 2.404 | 28.7 |
| arm-011 | +0.7708 | 2.542 | 27.1 |
| arm-100 | +0.7100 | 2.420 | 28.5 |
| arm-101 | +0.7155 | 2.431 | 28.4 |
| arm-110 | +0.6003 | 2.201 | 31.4 |
| arm-111 | +0.7100 | 2.420 | 28.5 |

**Median 0.7061** (0.5277–0.7708). The paper's corpus: **0.530**. Every one of our eight arms is at
or above it, and the median is 33% higher — so importing their number would have understated us.
A correlation is a property of a corpus, which is the reason to measure rather than cite.

## 2. The exposure the prediction warned about is not in our code

Audited every site that puts a confidence interval on a count:

| site | what its `n` counts | clustered correctly? |
|---|---|---|
| `eval/replicated.py` → `compare_paired` | **tasks** — arms are paired on per-task `pass^k` | **yes**, and its docstring already said so |
| `eval/swe_bench.py::compare_arms` | instances, one row each | yes |
| `eval/bench_ab.py::compare` | whatever the caller passes; both callers pass one row per item | yes |
| `eval/context_curve.py` | runs binned by context size | one row per run, not per replica |
| `evolution/auto_evolve.py` | **panel models on one shared input** | **no — see §5** |

So the headline of §2 of the sweep — *"anywhere we compute a CI over k outputs that passed the same
gate, the margin is narrower than the arithmetic says"* — **is refuted for the replicated-eval path
it named**. Pairing on per-task `pass^k` absorbs the within-task correlation into one observation per
task; a task contributes once however many times it ran, and there is nothing left for a design
effect to discount. That was checked, not assumed.

**A correction to our own correction, then.** Study 18 produced six corrections to our record; this
one over-stated our exposure by naming a path that was already right. Reading the paper told us the
mechanism exists. Only reading our code could say whether we had it, and the sweep skipped that step.

## 3. What the number is actually for: replicas are priced, not free

The design effect does not widen any interval we publish. It prices the thing that was bought:

| k | design effect | independent observations | **what the k-th run added** |
|---:|---:|---:|---:|
| 1 | 1.000 | 1.000 | 1.000 |
| 2 | 1.706 | 1.172 | 0.172 |
| 3 | 2.412 | **1.244** | **0.071** |
| 5 | 3.824 | 1.307 | 0.064 |
| 8 | 5.943 | 1.346 | 0.039 |

**Three runs of a task carry 1.24 independent observations, not 3.** The third run bought 0.07 of an
observation.

## 4. The same 552 solves, spent differently

The factorial's own budget, reshaped at our measured correlation. **The suite has 106 tasks; we used
23**, so every row below was available at the time:

| shape | clusters | effective observations per arm | |
|---|---:|---:|---|
| 23 tasks × 3 replicas | 23 | **28.6** | what #453 ran |
| 35 tasks × 2 | 35 | 41.0 | |
| 69 tasks × 1 | 69 | **69.0** | same money, 2.4× the information |
| 8 tasks × 3, plus 45 tasks × 1 | 53 | **54.9** | keeps a floor **and** buys breadth |

**But the last row, not the third, is the rule** — because replicas were never bought for precision.
They are how a noise floor is measured at all, and `replicated.py` encodes exactly that: *one run is
a sample, two alert, three decide* (§2x). At k=1 there is no within-task variance and no floor, and
#453's central finding — that all three factors sat inside a noise floor of SD 0.073 — could not
have been stated.

So the design rule is not "fewer replicas". It is: **replicate a subset deep enough to measure the
floor, and spend the rest of the budget on more tasks.** Eight tasks at k=3 give the floor; the 45
tasks that money would otherwise have bought a third run for nearly double the effective n.

Applied to #453 as it ran: it paid for 23 tasks × 3 and received the information of 28.6, where
53 clusters were reachable for the same US$29.

## 5. The one site that is genuinely un-clustered

`evolution/auto_evolve.py` gates an accepted skill on `wilson_lower_best_of(passed, n, k)`, where
`n` is the number of **transfer models in the panel**, each run once against the **same** test input.
Those verdicts are correlated by the input they share — which is `2609.10969`'s finding in this
project's own words: cross-model voting over shared evidence approves 62.9% of unsafe proposals
against 22.9% with independent sources.

The direction is the uncomfortable one. An inflated `n` makes a Wilson **lower** bound higher, so
the gate is more permissive than its arithmetic claims, on the one path that accepts self-evolved
skills into the store.

**Nothing is changed there in this PR, and that is deliberate.** The ICC measured above is for
*replicas of an arm across tasks*; the panel's correlation is a different clustering and this corpus
cannot estimate it (§6). Applying 0.706 to that gate would be importing a number from the wrong
population — exactly the error §1 exists to avoid. What the panel's agreement actually is remains
**unmeasured and now named**.

## 6. What this cannot show (§2q)

- One benchmark, one model (`deepseek-v3.2`), one threshold (outcome ≥ 0.8). ICC is a property of
  the corpus and the grader, and a different suite will have a different one.
- **Binary outcomes.** `icc1` is the standard approximation on pass/fail; the oracle scores are
  continuous and thresholding throws information away before the statistic sees it.
- Eight arms are not eight independent estimates — they share the same 23 tasks and the same model,
  so the 0.53–0.77 spread is a range, not a sampling distribution.
- **The evolution gate's correlation is not estimated here**, only located.

## 7. What ships

`chimera.eval.replicated.design_effect`, and one line added to `format_replicated_report`:

```
runs per task       k=3: decides — three or more runs bound the noise the comparison is read against
what k bought       ICC +0.71 -> design effect 2.41, so 3 runs per task carry 1.24 independent
                    observations, not 3; a 4th would add 0.04
```

A report that says "k=3: decides" and stops invites the reading that three runs are three
observations. Now the next person choosing a k sees its price in the same block where they chose it.

`bench/PROTOCOL.md` gains §8 with the rule and this as its measured instance.

## 8. Reproducing

```bash
cd bench/design_effect && python3 measure.py    # every table above, seconds, no spend
```

Needs the factorial's `~/harness-bench` in place. `results.json` carries the numbers.
