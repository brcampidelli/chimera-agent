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

- **Runner**: `bench/swe_bench/run_swe.py`, arms `roles_single` / `roles_balanced` / `roles_max` —
  the same runner and the same sanitised-checkout recipe the scaffold runs used. (This said
  `chimera solve-batch` when written, which was simply wrong: `solve-batch` is not what produces
  SWE-bench predictions, and naming a tool the bench does not use is how a reader ends up unable to
  reproduce it.)
- **Per-solve timeout: 1800 s**, matching runs 2–4 (`bench/swe_bench` Amendment 2). Fixed here
  because it was NOT fixed here: the costing pilot inherited `run_swe.py`'s 900 s file default by
  accident and censored the expensive arm at 4/4 while the cheap one lost 2/4 — an asymmetry the
  `swe_bench` pre-registration names as invalidating. A timeout the document does not state is a
  timeout that gets chosen by whatever the file happened to say.
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
intervention has room to show anything, and that failure mode is a standing rule in this repo:
**a suite whose baseline sits above ~75% cannot answer this question**, and if A0 lands there the
run is reported as uninformative rather than mined for a subgroup that isn't.

> This originally read "outside 40–60%". The floor was **withdrawn by Amendment 2** — it was
> imported from a ceiling argument that never supported it, and it would have declared runs 3 and 4,
> the two most informative this project has done, uninformative. The ceiling stands; a *low*
> baseline is not a disqualification. What decides informativeness is the discordant-pair count in
> the next paragraph, which is what McNemar actually consumes.

n = 41 paired tasks per arm. Powered to detect a large effect only, and that is stated up front:
with ~41 pairs, McNemar reaches p < 0.05 at roughly **10+ discordant pairs breaking 8:2 or better**.
Anything smaller than that will be reported as *inconclusive*, not as a trend.

## 5. What is recorded, per task, before any of it is looked at

From the receipts that already exist — nothing new is invented to make this measurable:

| Field | Source |
|---|---|
| passed | the verify command's exit code |
| attempts | `AutonomousResult.attempts` |
| usd | worker + overhead, via `Attempt.usd`; **null when any leg's price is unknown** |
| `overhead_usd` | the non-worker share — planner, manager, checklist, strong-verify |
| tokens | prompt / completion, per role |
| `route_meta` | which model actually answered each turn, and why |
| diff size | `Attempt.diff_summary` |
| `diff_productive` | the diff gate — an empty-diff "success" is not a success |

Cost is reported as **unknown** whenever any leg's price is unknown, never as zero and never as a
partial sum. A cost comparison that silently drops the free tier's contribution would make the cheap
arm look cheaper than it is, in the direction that flatters the feature.

`overhead_usd` is split out, and it is not a nicety: when this document was written, the planner and
the manager were priced **nowhere** — the receipt carried the worker alone. A model-per-role profile
differs from single-model mostly in who plans and who reviews, so the row that would have decided
this bench's cost comparison was the one row nobody was recording. Fixed in
`chimera/orchestration/metering.py` before any arm runs; without that fix, every number in this
table would have understated the expensive arms by exactly the amount that makes them expensive.

## 6. Readings that would make this feature not worth its default

Named now, so none of them can be reframed later as "a different question":

1. **A1 ≈ A0 with A1 costing more.** Routing is a knob, not a default. The profile selector stays,
   the claim does not, and the README says routing is unproven.
2. **A2 > A1 > A0 monotonic.** The effect is model strength, not roles. The honest headline is "a
   stronger editor helps", which is not news, and the multi-role framing is dropped.
3. **A1 wins only on `diff_productive=false` tasks.** That is the diff gate catching hollow
   successes, not routing working.
4. **A0 above 75%.** Uninformative run — no headroom left for routing to convert. Reported, not
   repeated with a different suite until it lands where I want it. (Was "outside 40–60%"; the floor
   is withdrawn by Amendment 2, and A0's best available estimate — 39.0%, Wilson [25.7%, 54.3%] —
   is *expected* to sit below where that floor was.)
5. **Fewer than 10 discordant pairs.** Inconclusive. The interval is published including the part
   of it that crosses zero.

## 7. Amendments

Any change to the arms, the suite, the statistic or the band is an amendment committed **before** the
run that uses it, in this file, with its reason. An amendment made after seeing a result is a
retraction of this document, and will be labelled as one.

### Amendment 2 — retracting the 40% floor, which I imported from an argument that never supported it

**Labelled a retraction, per §7.** It changes a criterion in §4 and §6.4, and I am the one it
benefits, so it is named for what it is rather than filed as a tidy-up.

**What is wrong.** §4 justifies its band with one failure and one only: *"three authored suites in
`learning_lift` all landed at 84–92% pass, where no intervention has room to show anything."* That
is a **ceiling** argument. A baseline near 100% leaves nothing for an intervention to convert. It
says nothing whatever about a baseline being too low — and yet I wrote a symmetric 40–60% band, as
if the floor had been argued for. It had not. I took a one-sided concern and registered a two-sided
rule.

**The evidence against the floor was already published in this repo when I wrote it.** From
`bench/swe_bench/RESULTS.md`, run 4, on this exact 41-instance slice:

| arm | rate | Wilson 95% |
|---|---|---|
| baseline | 14/41 = 34.1% | [21.6%, 49.5%] |
| scaffold (**identical flags to my A0**) | 16/41 = **39.0%** | [25.7%, 54.3%] |
| scaffold + gate | 18/41 = 43.9% | [29.9%, 59.0%] |

Both of run 4's comparisons ran against a **34.1%** baseline — well under my floor — and each
produced **8 discordant pairs** (+5/−3 and +6/−2), the quantity McNemar actually consumes. Run 3,
also at 34.1%, is the out-of-sample replication this project's headline rests on. My floor would
have declared the two most informative runs this project has ever done **uninformative**.

**And 39.0% is a point estimate with a 29-point interval.** [25.7%, 54.3%] straddles the band.
Declaring a run dead because a point estimate sits 1.0 point under a threshold is exactly the
over-reading of a small-n number that the rest of this document spends its length refusing.

**What replaces it.** Nothing new — the criterion that does this job was already in §4, one
paragraph below the band: *"McNemar reaches p < 0.05 at roughly 10+ discordant pairs breaking 8:2 or
better. Anything smaller will be reported as inconclusive."* Discordant pairs are what the statistic
consumes, and they depend on the effect, not on where the baseline happens to sit. The band was
redundant with a better rule and weaker than it. So:

- The **floor is withdrawn.** A low baseline is not a disqualification.
- The **ceiling stays**, because its argument is real and one-sided: if A0 exceeds **75%**, the run
  is reported as uninformative — there is not enough headroom left for a routing effect to appear.
- Informativeness is decided by **discordant pairs**, as §4 already said.

**Why this is not "the number was inconvenient, so I moved the line."** The test is whether the
argument survives the number changing. It does: the floor was imported from a ceiling argument
regardless of what A0 turns out to be, and runs 3 and 4 contradicted it before this bench existed. I
did not discover a new fact; I failed to apply one that was already in the file I cited. What the
inconvenient number did was make me look. That is a real difference from the redesign I proposed
earlier and withdrew — that one reversed a principle I had written down *in this document*, and
reversed it in the direction of a cheaper run.

**What this does not change.** Not the arms, not the slice, not the statistic, not §6's other four
sinking readings. §6.4 is rewritten from "A0 outside 40–60%" to "A0 above 75%"; everything else in
§6 stands as registered.

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
