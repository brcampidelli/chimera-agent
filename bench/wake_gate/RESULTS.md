# The wake gate on the cron daemon — results

**2026-09-29.** Run against `PREREGISTRATION.md` (written 2026-09-28, before any call), the corpus
`items.jsonl` labelled before the first call, local `qwen3:4b@Q4_K_M`, US$ 0 (34 scored calls +
34 repeat calls + 4 diagnostic calls, all local). Runner: `run.py`; raw rows:
`results/wake_gate.jsonl`; machine summary: `results/summary.json`.

## The verdict, by the rule fixed before the numbers

**NULL.** False skips = 0 (the safety bar, met at every swept floor) but the suppression rate is
**0/17 = 0%**, far under the ≥ 50% the rule requires. Per the registered decision rule: no shadow
verdict is written, the daemon is unchanged, and the gate exists as a spec and a corpus. The
results say the mechanism did not separate our traffic — which is a result, not a failure of the
bench: the corpus, the answers and the miss are published below.

| registered bar | measured | met? |
|---|---|---|
| false skips = 0 (kill line) | **0** (at floor 0.2; 0 at every floor 0.1–0.5) | **yes** |
| suppression ≥ 50% on not_yet/unrelated | **0/17 (0%)** | **no** |
| → decision | **null** — no shadow arm, daemon unchanged | |

## The numbers

* **AUROC wake vs sleep: 0.796** (bootstrap 95% CI **0.636–0.933**, 2000 resamples, seed 7) —
  inside the registered prediction band (0.70–0.85). The question ranks; the policy never gets to
  use the ranking, because the ranking lives in the *probability* and the model's *choices* are
  almost always `wake`.
* **The choice distribution is the finding.** 31 of 34 scored scenarios answered `wake` —
  including all 6 `unrelated` (stripe invoice, CI green, backup finished, newsletter, another
  job's alert, deploy elsewhere) and 8 of 11 `not_yet`. Only 3 scenarios drew `not_yet`
  (p_wake 0.35–0.47): the three with the most repetitive, self-contained state (the same answer
  again, a tick minutes after the last one, the base case of the real history).
* **The sweep says the same thing from the other side:** no put-down choice carried wake-mass
  ≤ 0.5 except those same 3 — so even a floor of 0.5 suppresses only 3/17 (17.6%), and every
  floor below that suppresses nothing. There is no operating point on this instrument that both
  keeps false skips at 0 and reaches 50% suppression: the model does not produce confident
  put-downs on this state.
* **Latency:** p50 0.34 s, p95 0.38 s per call (one 5.5 s cold start); mean 267.6 prompt tokens,
  max 304 — the state is small, as designed. Cost per 1,000 ticks at the stated hosted
  assumption (US$ 0.10/1M input): **US$ 0.027** — an assumption beside a number, not a
  measurement; the local call is US$ 0.
* **T baseline (always wake):** 34 wakeups, 0 suppressed, 0 calls. The gate as measured adds
  34 calls and saves nothing — the honest one-line summary of the null.

## The repeat arm found something the first pass hid (§Items: "any flip is reported")

Two scenarios flipped **between identical greedy calls** — not in choice, in probability:

| scenario | pass 1 | pass 3 (diagnostic) |
|---|---|---|
| `resumo-01-timer-unchanged` | wake, p 0.836 | wake, p 0.768 |
| `resumo-02-timer-same-again` | wake, p 0.556 | wake, p 0.643 |

Choices stable (wake/wake both passes), p drifting ±0.09 on a state that should be
deterministic. `bench/decisions_v2/RESULTS.md` measured 55/55 identical greedy answers on this
machine — but those were **Nouls** (yes/no, the label token's mass read directly); this is a
**three-way Choice**, and the drift is in the share arithmetic over three options, not in the
label token. Recorded as a fact about the instrument: a three-way local reading is less stable
than the two-way one every prior bench used, and any future map for this decision has to be
fitted against that noise. **Apparatus defect, named:** the repeat pass's answers were compared
and discarded inside `run.py` instead of being written to `results/` — the two pass-3 readings
above were re-measured afterwards, by hand, to characterise the flips (4 local calls, US$ 0).
The runner should persist every pass; it now does not, and that is this results file's defect to
carry, not to hide.

## Why the model never sleeps (the miss, named as the rule requires)

Reading the 34 rows: the state carries **what the job is for** (its action text) and **what
happened** (the event, the last result) — but the model reads "something fired" as itself a
reason to wake. The three scenarios it put down are exactly the ones whose state says *nothing
new* in its own text ("idêntico à execução anterior", a tick 4 minutes after the last). The
wakegate table this design adopted assumes a model that produces confident put-downs; on our
state, with a 4B local model, the put-downs are rare and never confident. The mechanism's
premise — that the cheap model's *choice* carries the signal — is what failed here, not the
thresholds: the AUROC says the probability ranks fine (0.796), so a policy that read the
**probability** against a swept cut instead of the **choice** would have material to work with
(p_wake on the 17 sleep-labelled rows spans 0.35–0.95, overlapping the wake rows' 0.54–0.995).
That is a different policy and a different bench — this registration's rule is fixed, its
verdict is null, and the probability-reading variant is named here as the next candidate, not
smuggled in as this bench's result.

## What ships

Nothing. The daemon is unchanged; no shadow verdict lands in `cron_results.jsonl`; the spec
(`scheduler.wake`) stays unregistered in the product. What this bench leaves behind: the corpus
(36 labelled scenarios over the owner's real job), the instrument (lint-clean, byte-for-byte in
the registration), the runner, and the two measured facts any successor needs — the ranking is
real (0.796) and the choice-based policy is empty (0/17). The enforce question is moot: there is
nothing to enforce.

## What this cannot show (carried from §2q, unchanged)

Whether suppression is worth dollars on real traffic (moot here); anything about a hosted
backend; ground truth beyond one labeller who wrote the scenarios; any job type outside the
owner's single active cron job — **which is now the sharpest limit**: 34 of 36 scenarios are one
job's traffic, and the null is a null *on that job*. A second job with a different shape (a
guard, a fills check, a watcher with real webhook traffic) could rank differently, and the
corpus is built to take those rows.