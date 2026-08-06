# Costing pilot — what an instance of each arm actually costs

**This is not the experiment.** The experiment is `PREREGISTRATION.md`, and it has not been run.
This document records a pilot authorised by Amendment 1 for one purpose: turn the registered run's
budget from a guess into a number. Cost and wall time are the readings. **Outcomes are not evidence
here** and the pass/fail column is deliberately absent from every table below — with n=1 per arm,
reporting it would be inviting exactly the reading the pre-registration forbids.

Two pilots ran. The first bought no cost number and three defects instead; the second is the one
whose numbers are reportable. Both are recorded, because the first is the reason the second is
instrumented the way it is.

---

## Pilot 1 — 2026-08-04 — US$ 5.25 spent, zero cost numbers produced

Four things were wrong at once, and each one alone would have invalidated the reading.

**The harness never called a model.** The runner loaded `.env` from a file synced from Windows, so
every value carried a trailing `\r`. Pydantic rejected `CHIMERA_CHAT_MEMORY=false\r`, which killed
each `chimera` invocation before its first model call. All 12 solves returned empty in 0 s. Fixed by
stripping CR at sync time.

**The fused roles ran a frontier panel nobody selected.** `_fused_if` built `FusionEngine(gateway)`
and discarded the role's model, so every fused turn fell through to the default panel — Opus,
GPT-5.5, Gemini — rather than the profile's tier ladder. This shipped the same day as the profiles.
Fixed in `fusion_for_role` (`chimera/api/roles.py`), which now builds the config from the *passed*
settings and overrides panel/judge/synthesizer from the ladder.

**Everything that is not the worker was priced nowhere.** Planner, manager, requirement checklist and
strong verifier call `backend.complete` directly, and no receipt counted them. The error is
*directional*: a role profile exists to put a stronger model on planning and review, so the more a
profile spent on what distinguishes it, the more of that spend was invisible. Fixed by
`MeteredBackend` (`chimera/orchestration/metering.py`).

**The timeout was 900 s, not the registered 1800 s.** Four of the twelve solves were killed by it.

### Two claims made from pilot 1 and retracted

> *"roles_balanced costs 3.2× roles_single."*

Withdrawn. That was a **censored residual**: the numerator had 4/4 solves killed by the timeout, the
denominator 2/4, the third arm never ran at all, and the configuration being measured was the
frontier-panel bug rather than the profile. Two independent defects in one number.

> *"roles_single finished normally."*

Withdrawn. Two of its four solves timed out; I multiplied a 2-instance rate by 4.

A third correction is procedural rather than numeric. After seeing an inconvenient invoice I argued
for matching the arms' budgets — having written in §2 of the pre-registration that forcing a budget
match "is the amendment this document exists to forbid". Withdrawn as rationalisation.

### The instrument defect underneath all of it

A run killed by the per-solve timeout **writes no receipt**. Internal accounting therefore reported
US$ 0.48 against US$ 5.25 actually billed — an 8× undercount, and one that gets *worse* the more
expensive the arm, because expensive arms time out more. Any cost ledger that trusts receipts is
measuring the runs that finished, which is not the same population as the runs that were paid for.

**This gap is open.** `runs.jsonl` still has no "started" line, so a run killed by the timeout or by
SIGKILL is an undeclared absence rather than a declared gap, and `worth.py` cannot report lost runs.
Until that is closed, the ledger below reads the provider, not the receipts.

---

## Pilot 2 — 2026-08-05 — method

Three changes, each answering one of the failures above.

**Ground truth for cost comes from the provider.** `run_pilot.py` reads OpenRouter's own balance for
the key before and after each arm. Censored work is still billed work, and the provider bills it
whether or not a receipt was written.

**Round by round, all arms per round.** One-arm-at-a-time spends the whole budget on the first arm
if that arm is the expensive one — which is the arm you cannot afford to be wrong about. Rounds keep
n equal across arms, so whatever the budget buys stays paired.

**It stops itself.** Before each round it re-reads both the spend and the cap, and stops if the
remaining room does not cover a worst case where every arm runs to the timeout at the last measured
burn rate. A reserve of US$ 0.40 stays unspent: a key cap is a backstop, not a plan, and hitting it
mid-solve leaves a half-run nobody can attribute.

The worst-case seeds start pessimistic (from pilot 1's inflated numbers) and are replaced by
measurement after round 1, so the guard tightens as it learns.

Per-solve timeout **1800 s**, matching the registered value. Instance slice:
`results/slice_pilot.jsonl`.

---

## Pilot 2 — readings

Three rounds ran — `django-11964`, `django-12125`, `django-12193` — giving **n=3 per arm**. Cost is
the **provider's** delta across each arm's window (`results/pilot_cost.json`). Total US$ 3.175.

| arm | n | US$/instance | s/instance | timed out | empty patch |
|---|---|---|---|---|---|
| `roles_single` | 3 | **0.3748** | 1107 | 0/3 | 0/3 |
| `roles_balanced` | 3 | **0.5256** | 1633 | 2/3 | 0/3 |
| `roles_max` | 3 | **0.1579** | 1804 | **3/3** | **3/3** |

### `roles_max` is not cheap; it is not running

Every `roles_max` solve hit the 1800 s wall with an empty patch. US$ 0.16 over half an hour is not
efficiency — it is a profile that spends its wall clock waiting instead of working. `resolve()` shows
why: **`max` puts `deepseek-r1` on the `edit` role.**

That contradicts the design rule this feature was built from — edit is the role that carries tools on
every turn, so it needs a model that tool-calls well, and a reasoning model spends the turn thinking
instead. The profile table in `roles.py` says edit is never fused for the same reason; putting a
reasoning model there reintroduces the cost the no-fusion rule was avoiding.

**`roles_max` must not enter the registered A/B in this shape.** An arm that cannot produce a patch
measures the wall clock, not the routing.

**Fixed** in `chimera/api/roles.py`: `max` now keeps `edit` on the tool-calling tier and escalates
only the roles where reasoning is the job. Two consequences worth stating plainly. The fix removed a
model collision that had been silently degrading `max`'s reviewer to the run default, so `max` now
gets a genuinely independent reviewer. And with a three-rung ladder and `edit` pinned to the middle
rung, `max` now differs from `balanced` only in the explore model and in fusing review — a smaller
step up than the name advertises. **The pilot's numbers for `roles_max` describe the old shape and
do not carry over.** Whether the new shape finishes at all is unmeasured.

### The receipt-based numbers reported earlier were wrong, in the flattering direction

An earlier reading of this pilot used receipts and reported `balanced` as **16 % cheaper** than
`single`. The provider says `balanced` costs **1.40×** `single`. Both statements cannot be true and
the provider is ground truth.

The gap is not only the censored runs. It is present on runs that **completed and wrote a receipt**:

| run | receipt US$ | provider US$ | receipt misses |
|---|---|---|---|
| `single` / 11964 | 0.3583 | 0.4177 | 14 % |
| `balanced` / 11964 | 0.2998 | 0.4581 | **35 %** |
| `single` / 12125 | 0.2297 | 0.3124 | 26 % |

So `MeteredBackend` closed part of the hole and not all of it, and what remains is still directional
— the profile that routes more roles is undercounted more. Any figure derived from receipts,
including the "overhead share" of 1.8 % vs 21.6 % quoted earlier, is a **lower bound on the routed
arm's cost**, not a measurement of it.

**Only 4 receipts exist for 9 runs.** The 5 missing are exactly the 5 timeouts.

### A censored run nearly got credited with the next run's number

`roles_max` wrote no receipt on any of its three solves, having timed out on all three. Its cost is
known only because the provider bills censored work — the gap firing for the fourth time.

Mid-pilot, the receipt that appeared after round 1's max was **not** the max arm. It was round 2's `roles_single` on a
different instance (`django-12125`). Nothing in `runs.jsonl` says which arm wrote a row: the receipt
carries `profile` only when a profile was passed, so the two single-model arms are both `None` and
the only thing distinguishing rows is **file order** — which stops being a valid identifier the
moment a run drops out. Reporting the row that follows a censored run as though it were that run is
a one-step mistake, and it is available to anyone reading this file the same way.

**Consequence for the ledger:** arm attribution must come from `pilot_cost.json` (which records
`{instance, arm, usd}` from provider deltas) and never from receipt order.

### What the ledger does and does not say

**It says what a registered run would cost.** At US$ 0.37–0.53 per instance per arm for the two arms
that work, a 40-instance paired A/B on two arms is roughly **US$ 36**, plus whatever share of runs
hit the wall. That number is the pilot's whole purpose and it is now measured rather than guessed.

**It says the routed arm is more expensive and slower**, on this slice: 1.40× the cost and 1.5× the
wall time, with 2 of 3 solves hitting the timeout against 0 of 3 for single-model. Under §2 of the
pre-registration that is not disqualifying — an arm is allowed to cost more if it buys the cost back
in passes — but it does raise the bar the routed arm has to clear.

**It says nothing about whether routing works.** n=3, one repository, and the pass column is not
evidence under Amendment 1. The registered A/B remains unrun.

**It is cost per instance, not cost per solved instance.** Those diverge exactly when arms differ in
pass rate, which is the unmeasured quantity. `roles_max` is the caricature: cheapest per instance,
and zero patches.

### The one receipt-derived reading that survives

**The overhead share moves by an order of magnitude**: 1.8 % for `single` against 21.6 % for
`balanced`, on the one instance where both wrote receipts. That is the profile doing what it is built
to do — shifting spend into planning and review — and the column did not exist before the metering
fix, so no earlier cost comparison of these profiles measured the part that distinguishes them.

It survives as a *direction*, not a magnitude. Both numbers are lower bounds, the routed one is
undercounted more, and it is a single instance.

The claim built on top of it — that the worker's saved cost more than paid for the extra planning —
does **not** survive. It rested on `balanced` being cheaper overall, which the provider contradicts.

---

## Known limitations of this harness

- **Prints are not flushed**, so the console log stays empty until the process exits. Progress has to
  be read from the receipts and the provider balance instead.
- **Timed-out runs still write no receipt**, so the `overhead` column is missing precisely for the
  runs where it would be largest. The provider total covers them; the split does not. Round 1's
  `roles_max` is the worked example: 1800 s of billed work, zero rows.
- **Receipts do not identify their arm.** `profile` is absent for both single-model arms, and
  `run_profile` is populated at the CLI but does not reach `runs.jsonl`. Order is not identity.
- **One instance is not a slice.** Per-instance cost varies with the repository, the test command and
  how early the diff gate rejects. Treat any single row as an order of magnitude.
- **The key is capped and disposable.** A pilot that can overspend is a pilot that can end an
  experiment it was supposed to budget.
