# SWE-bench Verified — run 1 (Q1 paired A/B): an exact zero

Design, slice, model, arms and predictions were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
(+ Amendment 1) **before any model call**. The result publishes as it came out.

**Bottom line up front.** On 19 gold-validated `django/django` instances from the easiest difficulty
stratum of SWE-bench Verified, driving `deepseek-chat-v3.1` through Chimera's scaffolding resolved
**exactly as many instances as the bare model**: 7/19 both, **Δ = +0.0%, 95% CI [−8.5%, +8.5%]**. Not
a "roughly equal" — literally 7 and 7, with one discordant pair each way. This is the project's first
number on an externally recognized scoreboard, and it is a **null**.

Three things keep this from being the verdict on the thesis, and all three are stated before the
numbers so they cannot read as excuses invented afterwards: the run **disabled verify-or-revert** (a
pre-registered choice, §Limitations 1) — the one mechanism behind the project's only positive result;
it ran with **`max_steps=8`** against a 250 MB repository (an oversight, not pre-registered,
§Limitations 2); and it used a **competent** model where the thesis is about lifting *weak* ones
(§Limitations 3). Most decisively, **17 of 19 pairs were concordant**, so the entire estimate rests on
**two** informative pairs.

## The result

```
n=19 django "<15 min fix" instances | model openrouter/deepseek/deepseek-chat-v3.1 | pass@1
  baseline (bare)     36.8%   (7/19)     --no-plan --no-manager --max-attempts 1
  chimera (scaffold)  36.8%   (7/19)     --repo-map --progress-ledger --replan --checklist --max-attempts 3

  paired Δ  +0.0%   95% CI [-8.5%, +8.5%]   -> NOT significant
  discordant: chimera-only 1 (django-11880) / baseline-only 1 (django-11163)
  concordant: both resolved 6 (10914, 11066, 11099, 11119, 11451, 11603) | both failed 11
```

Graded exclusively by the **official `swebench` 4.1.0 harness** in Docker (`FAIL_TO_PASS` must pass,
`PASS_TO_PASS` must stay green). Never self-reported. Raw reports:
`results/run1/chimera-{baseline,treatment}.run1_*.json`.

### Q1 and Q2, kept apart as registered

- **Q1 — the thesis (what this run answers).** Does Chimera's scaffolding beat the same model alone on
  the same instances? **No measurable difference: Δ = 0.0% [−8.5%, +8.5%].**
- **Q2 — the scoreboard (what this run does NOT answer).** 36.8% on a *deliberately easy, single-repo
  slice* is **not a SWE-bench Verified score** and will never be labelled as one. A Verified score needs
  the full 500 and remains deferred.

## Failure accounting (pre-registration rule 3)

Infrastructure failures are reported separately from genuine agent failures. **There were none.**

| | baseline | chimera |
|---|---|---|
| solves | 19 | 19 |
| produced a patch | 9 | 8 |
| **empty patch (no edit at all)** | **10** | **11** |
| resolved | 7 | 7 |
| **precision when it edited** | **7/9 = 78%** | **7/8 = 88%** |
| timeouts | 0 | 0 |
| infrastructure errors | 0 | 0 |

One instance, `django__django-10097`, was **excluded from the slice before any spend**: the gold
dry-run showed its own *reference* patch does not pass grading, so no agent patch could be judged
fairly on it. That is an infrastructure exclusion caught by gold, not an outcome-driven drop (commit
`4b28039`). The frozen slice went 20 → 19.

## Cost (pre-registration rule 4)

**US$ 0.82** for the full paired run (38 solves), plus **US$ 0.54** for the 3-instance cost probe.
Wall time: baseline 22 min (median 61 s/solve), chimera ≈ 46 min for the 10 measured solves (median
250 s/solve, max 411 s). The scaffolded arm is roughly **4× slower per solve** and produced *fewer*
patches. Timing for the first 9 chimera solves was lost when the run was paused mid-flight (telemetry
is written at completion) — reported rather than silently omitted.

## The one finding that is genuinely actionable

**Both arms are accurate when they act, and both usually don't act.** Precision when a patch exists is
78% and 88%; the dominant failure mode is producing **no edit at all** (10 and 11 of 19). Faced with a
real django issue, the model frequently answers in prose without touching a file.

That reframes the target. The bottleneck measured here is not "reasons badly" but **"does not commit
to an action"** — and neither scaffold addresses it. Both limitations below are direct candidate
causes, which is why the discriminating run (below) attacks exactly them.

## Limitations — why this null is not the thesis verdict

**1. Verify-or-revert was disabled (pre-registered, Amendment 1 decision 3).** Neither dataset ships a
runnable `test_cmd`, and synthesizing one from `FAIL_TO_PASS` would hand the agent its own hidden
graders. So the treatment arm ran with **no executable ground truth**. But `bench/local_lift` — the
project's *only* statistically significant lift — ran **with** `--verify`. This run therefore tested a
Chimera missing the mechanism behind its one proven win. The pre-registration flagged this ("tests a
weaker Chimera than local_lift did — noted, not hidden"); in hindsight it understated how much it
hollows out the test.

**2. `max_steps=8` against a 250 MB repo — an oversight, not a registered choice.** `chimera solve`
defaults to 8 tool-calling steps and the runner did not override it. Eight steps to navigate django,
locate the right file, and edit it is plausibly not enough; the observed signature (200–400 s elapsed,
no patch) is consistent with the step budget being consumed by navigation. This cannot be confirmed
retroactively — the runner discarded per-solve stdout — so it is a **hypothesis**, and the cheapest one
to test. It is recorded here as a methodological error of ours, not a property of the agent.

**3. A competent model, where the thesis is about weak ones.** `deepseek-chat-v3.1` was chosen to avoid
the floor that made `bench/terminal_bench` uninformative (37/40 both-fail). **It did not avoid the
floor** — 11 of 19 pairs were both-fail here — so the thesis-purity cost was paid without buying the
intended protection.

**4. Two informative pairs.** 17/19 concordant. McNemar reads only discordant pairs, so ±8.5% is what
two pairs buy. This is *uninformative*, not *disproving*.

## What this establishes, and what it does not

**Establishes.** The full SWE-bench pipeline works end to end and is honest: official Docker grading,
gold-validated slice, deterministic frozen instance set, sanitized checkouts (the `--no-tags` fix
mattered — django's `stable/*.x` tags reach the fix commit and would have leaked the answer), zero
infra failures, complete cost and failure accounting. And it produces a real, publishable external
number where the project previously had none.

**Does not establish.** That Chimera's scaffolding fails to lift models on real repositories. This run
tested a deliberately weakened configuration on a slice where both arms fail more than half the time,
with two informative pairs. The honest statement is: **on this slice, in this configuration, no lift
was measurable** — and the configuration was ours to choose badly.

## Next: one discriminating run

The two limitations under our control (1 and 2) are both fixable and both were listed as options in
[`PLAN.md`](PLAN.md) *before* any result existed: `--verify` on the repository's **existing** tests at
`base_commit` (option (b) — legitimate, no leakage of `FAIL_TO_PASS`) and a step budget matched to the
repo. That is a **different experiment** — full Chimera vs baseline, rather than weakened Chimera vs
baseline — pre-registered as Amendment 2, not a re-roll of this one.

**This null stands and is published regardless of how that run turns out.** If the discriminating run
is also null, the honest reading is that the scaffolding does not lift a competent model on real repos,
and the project's claim should be rewritten around what it demonstrably does: measure itself honestly.
