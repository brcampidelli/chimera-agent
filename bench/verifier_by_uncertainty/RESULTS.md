# Results — the strong verifier fired by uncertainty, with the signal that survived

*Run 2026-09-22, US$ 0, `python run.py` on the 547 factorial solves. Pre-registration: `PREREGISTRATION.md`, written before the run. Raw numbers: `results.json` (seed 20260913, 2000 draws).*

## The verdict in one line

**Null — D9 stays as it is.** Firing the strong verifier on the lowest-overlap 30% of claimed successes catches **5.8 more** false successes than flagging at random (48 against 42.2 of 139), and that difference is real — 95% CI over tasks **[+0.2, +12.4]**, permutation p **0.021** — but it costs **19.7 D9 calls per extra catch**, twice the registered ceiling of 10. A signal that is distinguishable from noise and not worth its price is a different null from "no signal", and the table says which one this is.

## 1. The shipped trigger, on this corpus (Q0)

| | |
|---|---|
| claimed successes at attempt ≥ 2 | **0 / 385** |
| D9 calls the shipped `index > 1` spends | **0** |
| false successes it catches | **0 / 139** |

**P0 confirmed.** Every run in the factorial has exactly one attempt (551 receipts: 387 `success`, 164 `exhausted`, all with `attempts` of length 1), so the trigger that gates D9 on a retry never fires. Two readings, both true:

- **About the corpus** (§2q): it cannot show what `index > 1` does under retries, and the comparison C2 asked for — "at the same number of calls the shipped trigger spends" — degenerates to zero calls. Any budget above zero beats it trivially, which is why the decision rule was written against `random` at equal budget instead.
- **About the gate's design:** a first attempt that claims success is the common case, and D9 never sees it. The gate as shipped verifies *retries*, not *claims*. That is a fact regardless of which signal would pick the items.

## 2. Catch at equal budget, ranked within task, leave-one-task-out

| budget | D9 calls | `overlap_low` | `overlap_band` | `random` (expected) | oracle ceiling | extra over random | calls / extra | 95% CI (tasks) | perm p |
|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| 10% | 36 | 13 / 139 | 15 / 139 | 12.5 | 22 / 139 | +0.5 | 71.3 | [−2.7, +3.9] | 0.509 |
| 20% | 78 | 30 / 139 | 34 / 139 | 28.8 | 45 / 139 | +1.2 | 66.1 | [−3.7, +6.2] | 0.392 |
| **30%** | **115** | **48 / 139** | 48 / 139 | 42.2 | 63 / 139 | **+5.8** | **19.7** | **[+0.2, +12.4]** | **0.021** |

Paired control: `overlap_low` reproduces the published 13 · 30 · 48 exactly (`bench/claim_vs_diff/results.json`); `random` reproduces the published ≈42 at 30%. Positive control 1.000. The permutation null of the extra sits at +0.02 to +0.04 — the ranker with shuffled labels catches what random catches, as it must.

## 3. Against the registered predictions

| | prediction | outcome |
|---|---|---|
| P0 | the shipped trigger covers 0 of 385 | **confirmed** |
| P1 | the 95% CI of the extra at 30% includes 0 | **refuted** — [+0.2, +12.4], p = 0.021. The +6 is inside no one's noise; it is small, not imaginary. At 10% and 20% it *is* inside the noise. |
| P2 | the middle band catches fewer than the bottom at every budget | **refuted** — 15 > 13, 34 > 30, 48 = 48. The uncertain middle of this ranker is at least as rich in false successes as its most-suspicious end: the arm's ordering carries signal in the aggregate and not at the extremes, which is what an AUROC of 0.66 looks like item by item. |
| P3 | neither condition of §6 met; null | **confirmed on the cost condition** — condition 1 (CI excludes 0, p ≤ 0.05) is met; condition 2 (≤ 10 calls per extra catch) is not, at 19.7. |

## 4. What this decides

- **`index > 1` stays.** Not because it is good — on this corpus it is inert — but because the registered replacement buys one caught false success per twenty full model passes, and the diff-gate that already runs is free. Bruno's rule, fixed before the run: "se não bater, publica-se o nulo e o D9 fica como está."
- **The next thing to change is the gate, not the signal.** A verifier gated on retries verifies nothing in a single-attempt deployment. If D9 is ever to catch a false first-attempt claim, it has to be fired on something other than the attempt index — and the only signal we hold that clears noise is this one, at a price this bench has now put a number on.
- **The B4 Noul stays out.** At chance (0.54) it would only have added calls.

## 5. What this cannot show (§2q)

- The shipped trigger under retries — a factorial with `max_attempts ≥ 2` would be needed.
- What D9 would *say* on the items sent to it: no verdicts of the strong verifier exist on these solves. This bench measures which items are sent, not whether verifying them helps.
- A calibrated `p(true_success)`: the overlap score is a logistic decision value with no map; the "band" here is a quantile band. When a calibrated reading exists, the decision contract (`chimera/decisions`) is where it goes, and this bench is the one to rerun against it.
- Whether P2's band-over-bottom ordering is real: 15 vs 13 and 34 vs 30 are within the noise §2 prints; it is reported because it was predicted the other way, not because it is established.
