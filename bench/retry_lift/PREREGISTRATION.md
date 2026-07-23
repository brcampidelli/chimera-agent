# Pre-registration — retry-lift: does conditioning a retry on its own failed attempt help?

**Written and committed BEFORE any model call of this run, and before any of the code it describes
exists.** Everything below is decided while the outcome cannot be known. No re-running to chase
significance; the result publishes whatever it says — including the embarrassing readings named in §6.

This is a **new question**, not a continuation of [`bench/learning_lift`](../learning_lift/RESULTS.md).
That series is closed: seven runs, run 6's positive retracted by run 7, and the conclusion that no
synthetic suite we author can measure *cross-task* transfer. This bench asks something that needs no
transfer at all.

## 1. The question

When an attempt fails, the agent captures the diff that attempt actually wrote (`Attempt.diffs` /
`diff_summary` in `chimera/core/autonomous.py`, snapshotted **before** the revert) — and then throws it
away. Every consumer is telemetry: API schemas, the run log, the trajectory logger, the CLI's display of
the *successful* fix. It never re-enters a prompt.

So on attempt 2 the model is told **that** it failed and **what to focus on**, but is never shown **the
code it wrote that did not work** — and since the workspace was reverted, nothing on disk records the
wrong path either. Nothing prevents it from re-deriving the same patch.

> **Does feeding the failed diff back — framed as a path not to retake — improve recovery?**

## 2. Why this is worth a run: the population was measured first

[`bench/learning_lift/probe_attempts.py`](../learning_lift/probe_attempts.py), n=40 cold solves on the
hard disjoint suite, zero timeouts, no test tampering (raw: `results_probe/probe.json`):

| | | 95% CI |
|---|---|---|
| overall pass rate | 27/40 = 67.5% | [52.0%, 79.9%] |
| **A** — attempt 1 failed | 21/40 = **52.5%** | [37.5%, 67.1%] |
| **R** — recovered after retrying | 8/21 = **38.1%** | [20.8%, 59.1%] |
| **X** — failures that used all 3 attempts | **13/13 = 100%** | — |
| attempts among passes / failures | {1:19, 2:5, 3:3} / {3:13} | |

Three facts justify the run. The mechanism's population is **more than half of all solves** (the
learning-lift mechanism's population on disjoint suites was ~zero — the structural difference). **Every
failure exhausts the budget**, so the retry loop is fully engaged and still losing, rather than failing
fast for some unrelated reason. And **R = 38.1% sits inside the 40–60% informative band that three
authored suites failed to hit** — because conditioning on "attempt 1 failed" selects the hard cases
automatically instead of us fabricating difficulty.

**Ceiling: +32.5% absolute [20.1%, 48.0%]** — what a *perfect* retry would convert. This is an upper
bound under an impossible intervention, not a prediction. Stated here so it cannot later be quoted as one.

## 3. Two interventions, deliberately separated

They act on the **same headroom** (the 13/13 failures that burn the full budget). Running only one and
finding a positive would leave the credit unassignable, so both get their own arm.

- **I1 — diff-feedback (the proposal under test).** Append the failed attempt's diff to the retry
  feedback, framed as a path not to retake.
- **I2 — stagnation-detection repair (the confound).** The existing anti-stagnation pivot already
  exists to stop repeated identical failures, but is plausibly never firing: `StagnationDetector(window=2)`
  (`chimera/cli/main.py`) against `max_attempts=3` gives it at most one chance, and `_assess_signatures`
  requires **exact string equality** of the first 400 normalized characters
  (`chimera/evolution/stagnation.py`). Two attempts failing from the same cause with different assertion
  text do not match. I2 = fire earlier and match approximately.

If I1 wins and I2 does not, the proposal is vindicated on its own terms. If I2 wins, the headroom was a
latent bug, not a missing capability — a cheaper fix, and the honest credit.

## 4. Design (fixed now)

| | |
|---|---|
| **Suite** | `bench/learning_lift/tasks_hard_fix.py`, all 40 tasks, committed order. Already validated (40/40 tests fail against their buggy source) and already characterised by §2 — **no new suite is authored for this run**, which removes the difficulty-tuning failure mode that ended the learning-lift series. |
| **Model** | `openrouter/mistralai/mistral-small-3.2-24b-instruct` — the same weak model as all seven learning-lift runs, so §2's numbers are the control's prior. |
| **Arms** | **control** = current behaviour, `--no-remember --no-collect --no-evolve-skills`; **I1** = control + diff-feedback; **I2** = control + stagnation repair. All three otherwise identical (`--repo-map --progress-ledger --checklist --replan --max-attempts 3`). No cross-task learning in any arm — a fresh agent home per task, so this measures the retry loop alone. |
| **Seeds** | **3** → n=120 solves per arm, 360 total. |
| **Primary metric** | **pooled paired Δ in pass rate** (I1 − control, and I2 − control), McNemar + Wilson CI on discordant pairs via `chimera/eval/paired.py` — the same estimator behind every published number here. |
| **Secondary metric** | **paired Δ restricted to retried solves**: tasks where **both** compared arms used ≥2 attempts (defined now, §5). The sharp lens on the mechanism. |
| **Instrumentation** | per-solve attempt count, duration, and timeout marker recorded — the data runs 2–7 discarded. |
| **Grading** | pristine test restored before every verdict; any arm that modifies its own test is recorded and graded against the original. |
| **Attempts** | one run per task per arm. No best-of-N. |

**The I1 framing and truncation are fixed here**, because they are the most temptingly tunable part of
the whole design:

> `You already tried this and it FAILED verification. This exact change was reverted:`
> `<unified diff, truncated to 2000 characters>`
> `Do not re-derive this edit. Diagnose why it was wrong, then take a different approach.`

Any deviation from this text or this cap is an amendment, committed before the run that uses it.

## 5. Definitions fixed in advance

- **Retried solve** — a solve whose recorded attempt count is ≥ 2, i.e. attempt 1 failed.
- **The retried-solve lens** compares only tasks where **both** arms in that comparison retried, so the
  pairing is genuine. Tasks retried by one arm only are reported as a count, never dropped silently.
- **Why conditioning on attempt-1 failure is legitimate:** the interventions inject context *only after
  a failure*, so neither can affect whether attempt 1 fails. Conditioning on it is therefore selection
  on a **pre-treatment** variable, not post-treatment selection, and does not induce collider bias. The
  arms may still retry different task sets by nondeterminism, which is exactly why the pooled metric is
  primary and the retried lens is secondary.

## 6. Registered predictions

Written before the run, so a wrong prediction is on the record rather than quietly rewritten:

- **I1 pooled Δ: +3 to +10 pp, probably NOT significant at n=120.** If R improves 38% → 55%, the pooled
  rate moves ~+9 pp (0.525 × 0.17); if R improves only to 45%, ~+3.7 pp, which n=120 will not resolve.
- **I1 on the retried lens: +5 to +20 pp on R.**
- **I2: smaller than I1, +0 to +10 pp on R.**
- **A real chance I1 is NEGATIVE — call it one in four.** Putting a wrong patch in context can *anchor*
  the model on it; negative examples are not reliably read as negative. This is the honest counter-
  hypothesis and it is registered as such.
- **Validity prior:** the control arm should reproduce §2 — A ≈ 52.5% and R ≈ 38.1%, within those CIs.

## 7. Validity gates (a run failing any of these measured nothing, and says so)

1. **The wire must be live.** Count how many retries actually received a diff in context (I1) and how
   many pivots actually fired (I2). **Zero = the intervention never happened**, and the run is reported
   as a plumbing failure, *not* as evidence against the idea. This gate exists because learning-lift
   runs 1 and 2 were spent measuring a disconnected loop that nobody had checked was connected.
2. **Timeout symmetry.** Per-arm latency is audited; asymmetric timeouts **invalidate the run outright**
   (the run-7a lesson: a silently swallowed timeout is indistinguishable from a capability failure).
3. **Control drift.** If the control's A or R lands outside §2's CIs, the run is measuring something
   other than what was sized, and the pooled result is reported-not-interpreted.
4. **Ceiling/floor.** If control R falls outside 20–80%, the retried lens is flagged uninformative-by-
   construction — the rule that flagged runs 1 and 6.
5. **Grading integrity.** Any test tampering is recorded per task and the workspace preserved.

## 8. The pre-committed readings

- **I1 pooled Δ > 0 and significant** → the proposal works, at the level that matters. The strongest
  available close.
- **I1 retried-lens Δ > 0 significant, pooled ns** → real on the mechanism lens, underpowered pooled.
  Report both; the retried lens is the primary evidence. **This is the most likely good outcome** and
  must not be inflated into the previous bullet — the exact overreach that produced run 6's retraction.
- **I1 null and I2 null** → the headroom is not retry-conditioning headroom. Those 13 budget-exhausting
  failures are capability-limited: a 24B model cannot clear the suite's authored traps no matter how the
  failure is fed back. Publish the null; it closes the question honestly.
- **I1 null, I2 positive** → the win was a latent bug in stagnation detection, not a missing capability.
  Credit goes to the cheaper fix, and the proposal is not vindicated by it.
- **I1 negative** → the anchoring risk was real: showing the model its wrong patch makes things worse.
  **This ships with the same prominence as a win**, and would be the most useful result of the four,
  because it is the one nobody would guess.

## 9. Caveats

One weak 24B model. One authored synthetic suite of 40 tasks — the same instrument whose *cross-task*
difficulty calibration failed three times, used here only because §2 characterised it directly and this
question does not depend on transfer. Three seeds is modest: a not-significant result is **underpowered,
not "no effect"**, and the CIs will say so. The retried lens conditions on a pre-treatment variable
(§5), which is sound, but the arms' retried sets will not be identical.

Nothing here generalises to real repositories; that is
[`bench/swe_bench`](../swe_bench/PLAN.md)'s job.

## 10. Stopping and reporting rules

1. **One run.** No re-rolls. If a run is discarded, the discard and its evidence are recorded here —
   as run 7a's was — because an undocumented discard is indistinguishable from a re-roll.
2. **Every number ships with its CI**, including the losses.
3. **A null ships with the same prominence as a win**, and a negative with the same prominence as a null.
4. **Cost is reported** — solves, wall time, and USD.
5. If the run is abandoned, **this file stays** and RESULTS.md records that it was not completed.

**Status: registered, not yet run. None of the code in §3 exists yet.**
