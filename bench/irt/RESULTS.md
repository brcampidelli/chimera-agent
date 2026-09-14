# Eleven of twenty-three tasks are constant AT A THRESHOLD I CHOSE — and the IRT fit that was supposed to rank the rest does not survive its own control

> ## ⚠️ RETRACTION, 2026-09-13, the same day
>
> **The first version of this file claimed the eleven constant items "reframe #453's null" because
> "the instrument had twelve live items". That is false, and the error was mine.**
>
> `bench/harness_bench/read_results.py` computes its main effects on the **continuous**
> `outcome_score`, per task, over **all 23 tasks** — `n_tasks=23` prints on every line of its
> output. The 0.8 threshold appears there only in the secondary flip-rate. The eleven tasks each
> contributed a real, varying per-task delta to #453's numbers.
>
> What had twelve live items is **this bench**, because **I** binarised at 0.8 to feed a 2PL. I
> presented a property of my own instrument as a property of the factorial. Checked afterwards:
> **nine of the eleven vary continuously** — 042 spans 0.150–0.740, 047 has 15 distinct values,
> 045 has 13 — and only 016 and 083 are genuinely flat at 1.000.
>
> Everything below about the **fit** stands: it still fails its own shuffle control, and the 7-of-8
> re-ranking still means nothing. What is withdrawn is every sentence that read the binarised count
> as a statement about #453.

**2026-09-13.** Study 18 shortlist item #12. **US$0** — a 2PL fitted over the Harness-Bench
factorial (#453) we already paid for: 24 respondents (8 arms × 3 replicas), 23 items (tasks), one
binary cell each at the same 0.8 oracle threshold every bench here uses.

## The verdict in one line

**Neither the count nor the fit says what the first version claimed.** Eleven of twenty-three tasks
are constant **once binarised at 0.8**, which is a fact about that cut and not about the factorial —
nine of the eleven vary on the continuous score #453 actually used. And everything the 2PL adds on
top reproduces at the same magnitude on **shuffled** answers, so none of it can be read either.

What survives is a methods result: **a 2PL over 24 respondents manufactures discrimination from
noise**, and **binarising a continuous DV to fit one costs most of the information before the fit
begins**.

## 1. What the data says without a model

| | |
|---|---:|
| items where **every one of the 24 respondents answered the same way** | **11 of 23** |
| items with any variance at all | 12 |

```
everyone passed:  016, 045, 051, 064, 083, 084
everyone failed:  042, 047, 086, 087, 092
```

Nearly half the tasks are constant **in this binarised view**. That is a count and needs no
estimator — but it is a count about the view, and the view is mine.

**It does not reframe #453's null, and the first version of this file said it did.** That bench's
main effects are computed on the continuous `outcome_score`, per task, across all 23 — its own
output prints `n_tasks=23` on every effect line. Measured afterwards, **nine of these eleven vary
continuously**:

| task | binarised | continuous range | distinct values |
|---|---|---|---:|
| 042 | constant | 0.150 – 0.740 | 3 |
| 047 | constant | 0.615 – 0.737 | **15** |
| 045 | constant | 0.817 – 0.991 | **13** |
| 086 | constant | 0.138 – 0.700 | 10 |
| 087 | constant | 0.287 – 0.675 | 6 |
| 016, 083 | constant | 1.000 – 1.000 | 1 — genuinely flat |

So the instrument that had twelve live items is **this one**. The factorial's had twenty-one.

## 2. The fit, and the control that kills it

A 2PL was fitted by joint maximum likelihood over the 12 live items. The discriminations look
informative — two tasks at 7.6 and 6.8, a long tail near zero, one negative:

| task | rate | discrimination |
|---|---:|---:|
| `089-ab-test-caveat-analysis` | 0.88 | 7.64 |
| `094-metric-definition-migration-diff` | 0.67 | 6.75 |
| `044-ci-config-repair` | 0.62 | 1.74 |
| … | | |
| `085-flaky-test-root-cause` | 0.58 | 0.02 |
| `022-local-rest-api-summary` | 0.62 | −0.33 |
| `011-code-debug` | 0.58 | −2.41 |

**Then the negative control.** Shuffling each item's answers among the respondents keeps every item's
rate and destroys every relationship between items — which is precisely what discrimination
measures. Under that null a 2PL should fit discriminations near zero. It does not:

| | real | shuffled within item |
|---|---:|---:|
| median \|discrimination\| | 0.62 | **0.66** |
| max \|discrimination\| | 7.64 | **8.50** |

**Joint MLE at 24 respondents manufactures discrimination out of noise, at the same magnitude as the
signal.** Every number in the table above is therefore unreadable, and so is the ordering it implies.

## 3. And the headline the paper offers cannot be tested here

arXiv 2609.09372 reports that choosing by aggregate rank displaces ~22% of appropriate picks. Ranked
by fitted ability against ranked by aggregate pass rate, **7 of our 8 arms change position** — 87.5%,
four times the paper's figure.

**That number means nothing, for two independent reasons, and both were known before it was
computed.** The fit it rests on fails §2's control. And #453 already established that all eight arms
sit inside the noise floor — so there was no true ordering for IRT to improve on. Reshuffling arms
that do not differ is shuffling noise, and a large displacement is what that looks like.

Reporting 87.5% as a confirmation of the paper, or as evidence that IRT ranks better, would have
been a number produced by an estimator applied past its range to arms that are indistinguishable.

## 4. What this changes

**Do not prune the bench by fitted discrimination.** That was the item's promise — "per-item
difficulty separates informative holdout items from noise" — and at our scale the separation the
estimator offers is indistinguishable from one it would offer on random data. Dropping `011` because
its discrimination came out at −2.41 would be dropping a task on the strength of a coin.

**Prune by variance — on the DV the analysis will actually use.** Two items are genuinely flat
(016 and 083, 1.000 everywhere) and a future run replaces those. The other nine are dead only to a
threshold, and the first version of this file said all eleven "moved no comparison", which was
wrong: they moved #453's comparison, because #453 never binarised them.

That sharpens `bench/design_effect`'s conclusion rather than replacing it. That bench said the money
goes into more tasks, not more replicas. This one adds: **more tasks that vary.** Twenty-three tasks
of which eleven are constant is a twelve-item instrument bought at twenty-three-item prices.

**And IRT is not closed, it is priced.** The estimator needs respondents, and 24 is not enough to
beat its own null. A future factorial with more arms, or the same arms over many more tasks, could
carry the fit; this one cannot.

## 5. What this cannot show (§2q)

- **One estimator.** JML is what a stdlib file can carry honestly and it is the one known to be
  biased outward at small n. Marginal MLE with an EM loop, or a Bayesian fit with a prior on
  discrimination, would shrink these estimates — the failure here is of JML at n=24, not of item
  response theory.
- **One null.** The shuffle preserves each item's marginal and randomises each respondent's. A
  stricter permutation preserving both marginals would be a harder null; this one is already enough
  to show the estimator manufactures discrimination, which is all that was needed to stop reading it.
- One benchmark, one model, one threshold — and **the threshold turned out to be the story**.
  Binarising a continuous oracle score at 0.8 throws information away before the fit sees it, a
  different cut gives different constants, and a claim about the binarised view is not a claim about
  the experiment that did not binarise. This file said otherwise for a few hours; see the retraction
  at the top.

## 6. Reproducing

```bash
cd bench/irt && python3 run.py     # the count, the fit, the control — under a second, no spend
```
