# Pre-registration — does the factorial's null hide strata of opposite sign?

**Registered 2026-09-15, before any partitioned number was computed.** Study 19, item A2.

## The claim being tested is ours

`RESULTS.md` reads the 2³ factorial as *"a null with power — none of repo-map / checklist / planner
beats the noise floor; simplify"*: A −0.012 [−0.036, +0.010], B +0.005 [−0.052, +0.052],
C +0.003 [−0.023, +0.029], every interval inside a within-cell SD of 0.073. (With 087 corrected
—#468— the numbers move by ≤ 0.001.)

Two outside measurements say an aggregate like that can hide structure:

- `2609.11987` ("Harness or Model?", 792 solves, 256 tasks): no harness wins on average, but
  **repository tasks −9.0 pp against contest tasks +23.7 pp, p = 0.003** by label permutation.
- `2609.13890` (DATS, 614 problems): the collaboration effect is **+2.4 pp in the easy tercile and
  +21.1 pp in the hard one** — scaffolding buys nothing where the model already passes.

Our factorial has **no task-type field anywhere** (`write_arms.py`, `read_results.py`, `results/`),
so the aggregate was read over a partition nobody looked at. The word "simplify" in `RESULTS.md`
reads as universal, and nothing measured says it is.

## Partitions — declared here, not chosen after looking

1. **Family**, from the benchmark's own `task.yaml: class`, which we did not write:
   *Software Engineering & Codebase Maintenance* — **16 tasks** — against everything else
   (*Data, BI & Finance Analytics* ×4, *Workspace, Tool Use & Multimodal Operations* ×2,
   *SRE, DevOps & Release Ops* ×1) — **7 tasks**. The benchmark's `difficulty` field is not used:
   six tasks have none and one says "medium-hard".
2. **Difficulty tercile**, by the **bare arm's** (arm-000) mean `outcome_score` over its three
   replicas: bottom 8 / middle 8 / top 7. A fixed rule applied to the data, computed once, with the
   087 correction applied first. The bare arm is the reference because the paper's moderator is
   "how far from the ceiling the model already is without help".

## Analysis

Same per-task deltas as `read_results.py` and `effects_with_087_corrected.py` (per-task mean per arm,
main effect = mean over with-arms minus mean over without-arms, per task). Then, per factor:

- the effect **within each stratum**: mean of that stratum's per-task deltas, bootstrap 95% CI over
  tasks within the stratum (10,000 draws, seed 20260915);
- the **interaction**: difference between two strata's means, bootstrap CI resampling tasks within
  each stratum independently;
- and the within-cell noise SD **per stratum**, because a null's power is its noise floor and the
  floor may itself differ by family.

## Predictions

- **P1 (the honest one).** With 16/7 and 8/8/7 tasks, most stratum intervals will be wider than the
  aggregate's and will still span zero. The expected reading is *"partitioned, not powered"*.
- **P2.** If the paper's pattern is here, the *planner* (C) is the factor most likely to show it —
  it is the one default-on factor and the one closest to "extra reasoning" — with a negative effect
  in the SE stratum and a positive one outside it, or negative in the top tercile and positive in the
  bottom.

## Decision rule

| stratum effect CIs | interaction CI | what `RESULTS.md` says afterwards |
|---|---|---|
| all span zero | spans zero | "null in the aggregate **and** in each declared stratum; not powered to see a ±0.10 interaction — *simplify* is what the aggregate supports, and the strata neither confirm nor deny it" |
| one excludes zero | spans zero | that stratum's effect reported with its n and CI; the aggregate sentence gains "in the *family* stratum the effect is …"; no claim about the other strata |
| any | excludes zero | the aggregate null is **withdrawn as a summary**: the factor helps in one stratum and hurts in another, and "simplify" comes out of the recommendations |

In every row, "simplify" stops reading as universal, because the register now shows which
partitions were looked at and what they had the power to see.

## Cost

US$ 0 — reads the stored result JSONs and the 087 re-grade. Nothing is written back into the
harness's files; this is a reading, like #468.

## What this cannot show

- An interaction smaller than the strata's own noise — which, at n = 7, is most interactions.
- Anything about task types the benchmark does not contain (there are no "contest" tasks here; the
  nearest analogue to the paper's split is SE-vs-other).
- Whether the tercile partition is confounded with family: the bottom tercile may be mostly one
  class. The reader prints the cross-tabulation so that is visible rather than assumed.

---

## Amendment 1 — 2026-09-15, written after the numbers were read

**Defect in this registration.** The analysis reads **six** interactions (three factors × two
partitions) and the decision table treats each 95% interval as if it were the only one looked at.
Nothing above says how to correct for six looks. This project already settled that question —
`chimera/eval/anytime.py` and study 10 use a Bonferroni split of the error budget across decisions —
and the sentence belonged here before the first number.

**How it is handled.** `effects_by_partition.py` now prints the Bonferroni-over-6 interval beside the
registered one, and `RESULTS.md` reports both. Because the correction was added after the data were
seen, it is **not allowed to decide**: the registered rule's outcome stands as "fired by the letter"
where it fired, and the corrected interval stands beside it as what the rule would have said had it
been written correctly. One interaction is affected (checklist, bottom-vs-top tercile:
+0.059 [+0.002, +0.122] registered; [−0.013, +0.144] corrected). Any replication registers the
correction first.

**Nothing else changed.** Partitions, tasks, seed, per-task deltas and the three predictions are as
registered; the numbers were computed once.
