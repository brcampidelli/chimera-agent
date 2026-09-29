# The stop gate — results

**2026-09-29.** Run against `PREREGISTRATION.md` (with its dated amendment, made before any call) on
the corpus and labels committed before the first call (`d19f75f0`). Local `qwen3:4b`, US$ 0.
- **Calls:** 118 turns × 4 questions. 2 of the 120 candidates were gated out by the belts.
- **How it ran:** as a background job of the desktop app (0.63.1, `run_shell(background=true)`).
- **Raw data:** answers in `results/answers.jsonl`, and the ablation below as `ablate` printed it.

## The verdict, by the registered rule: NULL

| registered bar | measured | met? |
|---|---|---|
| ΔAUROC(A3 − A1) ≥ +0.08 | **0.000** | no |
| lower bound of the Δ CI > +0.02 | **0.000** | no |
| wrong blocks ≤ 2% at the chosen cut | 2/120 at 0.65–0.85 | yes, but moot |
| → decision | **null**: nothing ships, not even shadow | |

All three arms read **AUROC 0.9805** (bootstrap 95% CI 0.950–1.000), identical to the 16th digit.

## Why the arms tie, and why the 0.98 is not the gate working

**The tie is real, not a bug.** Read from the recorded answers:
- A3's two extra mechanisms fired. The `outcome` veto (expectation < 0.5, i.e. mostly `blocked`)
  hit 48 turns, and the `verification_applies` floor hit 19.
- But every one of those turns already had a low `claims_done`, so capping them moved no rank.
- A2 differs from A1 only on the 2 gated-out turns, both negatives.

**The 0.98 is circular.** The labels and arm A1 read the same property:
- 118 of 120 edited turns ran no passing check (only 11 ran any check at all). So the rubric's
  third condition ("no check since the last change") is true almost everywhere, and `false_done`
  collapses into its first condition: "the message presents the work as finished".
- That is exactly what `claims_done` asks. Median `claims_done` is **0.94 on false_done** and
  **0.04 on everything else**. Of the 51 turns with `claims_done` ≥ 0.7, 49 are labelled false_done.
- The labeller and the decider agreed about what the message says. Neither was asked whether the
  work was broken.

**This is the §2q case:** the instrument could not have shown what the gate is for.
- A false done means work that is **claimed finished and does not work**.
- Nothing in this corpus says whether any of these 52 turns' work actually worked: no suite was run
  after the fact, and no later turn reporting a fix was joined in.
- limpet's wording-only null (AUROC 0.50) was measured against real outcomes. Ours was measured
  against a proxy that is the wording. The two numbers are not comparable, and 0.98 must not be
  quoted as the gate's accuracy.

## What would measure it

A successor registration, not an amendment:
- **Outcome labels from the future of each turn.** Did the next turn in the same conversation fix a
  bug in what this turn claimed done? Did the suite fail when run afterwards on the turn's commit?
  Either is ground truth the model cannot read off the final message.
- **A corpus where checks were sometimes run.** With 11 of 120 turns running any check, the evidence
  gate has nothing to separate. The terminal and desktop runs of this project are one source; the
  SWE-bench trajectories under `bench/default_model` are another, where every item has a graded
  outcome.
- **Keep the belts.** They are cheap and correct as designed. On this corpus they simply had no
  checks to find.

## Other facts recorded

- The three-way Noul wording amendment (compound lint) was made before any call and is dated in the
  pre-registration.
- p50 ≈ 1.8 s per turn for 4 local questions.
- The labels were written by a proxy labeller from the same model family as the author of the bench,
  and the 30-row second-labeller sensitivity check had nobody to run it. This matters less than the
  circularity above, which no second labeller would fix.
