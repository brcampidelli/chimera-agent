# Pre-registration — role routing: does a model per role beat one model for the same money?

**Written and committed BEFORE any model call of this run.** The routing code it describes exists
(`chimera/api/roles.py`, shipped with the Code screen's profile selector); the *measurement* does
not, and nothing below is decided after seeing a number. No re-running to chase significance. The
result publishes whatever it says, including the readings in §6 that would make the feature not
worth shipping as a default.

## 1. Why this bench has to exist before the feature is advertised

The Code screen now offers three profiles — economy / balanced / max — that put different models on
different roles (explore, plan, edit, review; verify has no model). The README-shaped claim writes
itself: *"Chimera routes each part of a coding task to the model best suited to it."*

That claim is currently **unmeasured**. Every competitor that says something like it is also
unmeasured, which is precisely why saying it without a number would put this project in the company
it spent the last month getting out of. The project's own history is the argument: `learning_lift`
ran seven times and retracted its one positive; `swe_bench` decomposed a lift into two mechanisms
and retracted two of my predictions. A feature that ships with a selector and no measurement is a
preference wearing the clothes of a result.

## 2. The question, stated so it can come back negative

> Holding the task suite and the verifier fixed, does **role-routed** solving reach a higher pass
> rate than **single-model** solving at comparable cost — and if it is more expensive, is the extra
> spend bought back in passes?

Two things follow from the wording. Pass rate alone is not the outcome: a profile that wins by
spending four times as much has not shown that routing helps, it has shown that a bigger model
helps, which nobody doubts. And "comparable cost" is reported, never enforced — forcing a budget
match would mean tuning the arms after seeing their cost, which is the amendment this document
exists to forbid.

## 3. Design: paired, same task, same verifier

Each arm solves the **same task** in its **own git worktree**, judged by the **same** verify
command. Pairing is what makes a small n readable: the two arms differ by routing and nothing else,
so per-task agreement/disagreement is the unit rather than two independent rates.

- **Runner**: `chimera solve-batch`, which already isolates each task in a worktree.
- **Statistic**: McNemar's exact test on the discordant pairs, via the existing
  `chimera/eval/paired.py` — the module that is already under mutation testing, so the arithmetic
  is not new code written by the person hoping for a result.
- **Arms**:
  - **A0 — single**: one model for everything (`--model <mid>`), the current default.
  - **A1 — balanced**: explore=weak, plan=top (fused), edit=mid, review=top.
  - **A2 — max**: explore=mid, plan=top (fused), edit=top, review=top (fused).

A2 exists to separate two explanations that A1 alone cannot: if A1 > A0 *and* A2 ≈ A1, the win came
from routing; if A2 > A1 > A0 monotonically, the win is mostly "a better model on the edit step",
and the honest headline is about model choice, not about roles.

## 4. Suite, and the band that decides whether the run is informative

**SWE-bench Verified**, the 41-instance out-of-sample slice already used in `bench/swe_bench`. Not a
suite authored here — three authored suites in `learning_lift` all landed at 84–92% pass, where no
intervention has room to show anything, and that failure mode is now a standing rule in this repo:
**a suite whose baseline sits outside 40–60% cannot answer this question**, and if A0 lands outside
that band the run is reported as uninformative rather than mined for a subgroup that isn't.

n = 41 paired tasks per arm. Powered to detect a large effect only, and that is stated up front:
with ~41 pairs, McNemar reaches p < 0.05 at roughly **10+ discordant pairs breaking 8:2 or better**.
Anything smaller than that will be reported as *inconclusive*, not as a trend.

## 5. What is recorded, per task, before any of it is looked at

From the receipts that already exist — nothing new is invented to make this measurable:

| Field | Source |
|---|---|
| passed | the verify command's exit code |
| attempts | `AutonomousResult.attempts` |
| usd | summed `AgentResult.usd`; **null when any model's price is unknown** |
| tokens | prompt / completion, per role |
| `route_meta` | which model actually answered each turn, and why |
| diff size | `Attempt.diff_summary` |
| `diff_productive` | the diff gate — an empty-diff "success" is not a success |

Cost is reported as **unknown** whenever any leg's price is unknown, never as zero and never as a
partial sum. A cost comparison that silently drops the free tier's contribution would make the cheap
arm look cheaper than it is, in the direction that flatters the feature.

## 6. Readings that would make this feature not worth its default

Named now, so none of them can be reframed later as "a different question":

1. **A1 ≈ A0 with A1 costing more.** Routing is a knob, not a default. The profile selector stays,
   the claim does not, and the README says routing is unproven.
2. **A2 > A1 > A0 monotonic.** The effect is model strength, not roles. The honest headline is "a
   stronger editor helps", which is not news, and the multi-role framing is dropped.
3. **A1 wins only on `diff_productive=false` tasks.** That is the diff gate catching hollow
   successes, not routing working.
4. **A0 outside 40–60%.** Uninformative run. Reported, not repeated with a different suite until it
   lands where I want it.
5. **Fewer than 10 discordant pairs.** Inconclusive. The interval is published including the part
   of it that crosses zero.

## 7. Amendments

Any change to the arms, the suite, the statistic or the band is an amendment committed **before** the
run that uses it, in this file, with its reason. An amendment made after seeing a result is a
retraction of this document, and will be labelled as one.

### Amendment 1 — a costing pilot, committed before it runs

**What.** Before the registered run, execute all three arms on **4 instances** drawn from the head of
the same slice, for one purpose: to measure **US$ per instance per arm**.

**Why.** §3 estimates the run's cost by multiplying run 4's measured US$0.316/instance by
multipliers for A1 and A2 that are guesses. That is the identical mistake run 4's own post-mortem
records — it estimated from run 3's *blended* rate instead of the comparable arm's, came in **52%
over**, and exhausted the budget mid-question. Estimating a second time from numbers of the same
quality would be choosing to repeat it.

**The constraint that makes this safe.** The pilot measures **cost only**. Its pass/fail outcomes
are **not evidence**, are **not reported** as a result, and are **not pooled** with the registered
run — n=4 could not support a claim in any case, and the reason to write this down is the other
direction: peeking at four outcomes and then choosing how to frame the main run is precisely the
degree of freedom this document exists to remove. If the pilot's instances are reused in the main
run, they are reused **in all three arms identically**, so the pairing is unaffected.

**What it can change.** Only the *budget decision* — whether the registered run is affordable, and
at what cap. It cannot change the arms, the slice, the statistic, or the band. If the measured cost
makes the three-arm design unaffordable, dropping A2 is a further amendment written **before** that
run, with its own reason, and it will say plainly that the result can no longer separate "routing
helped" from "a stronger editor helped".

## 8. Status

**Not yet run.** The code blocker is cleared: `chimera solve` now takes `--profile` /
`--role-models`, resolved by the *same* function the desktop endpoint uses (a bench driving a second
implementation of the routing would measure something the product does not ship), and the three arms
`roles_single` / `roles_balanced` / `roles_max` live in `bench/swe_bench/run_swe.py` alongside the
scaffold arms, differing from each other **only** in routing.

What remains is money. The SWE-bench phase was deliberately closed at US$0 remaining (see
`bench/swe_bench/RESULTS.md`). A US$10 costing pilot (Amendment 1) is authorised; the registered run
is not, and will not start until its measured cost is known and funded.

Until it runs, the profile selector ships as *a control*, described in the UI as a choice about cost
and models, with no claim that routing improves outcomes.
