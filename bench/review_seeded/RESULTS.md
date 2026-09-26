# Results: S15, `chimera review` on seeded defects

2026-09-25. Pre-registered in `PREREGISTRATION.md` (`afc0628e`), amended before the relaunch (`98c59c2d`). Product `3f5e5936`, prompt hashes `3a480dab0478` (finder) and `ee9d780a9f4b` (verifier), fixtures `b2f9e29a5fd3`, all recorded in `results/run.json`. Both arms were pinned to DeepInfra with fallbacks off. Measurement spend: **US$ 1.73** against a US$ 2.00 cap:
- run 1, discarded: US$ 0.686;
- run 2: US$ 1.039, of which D was US$ 0.073 and G US$ 0.966.

`results/report.txt` is the runner's own output. Every figure below comes from it, or from a hand read of `results/run.json`.

## The numbers

Wilson 95% intervals. A turn halts (and leaves the denominators) when its review came back `incomplete` or it stopped at the budget guard.

| | D₁ deepseek (default model) | D₂ same, replica | G glm-5.3 (the default reviewer) |
|---|---:|---:|---:|
| items run | 30 | 30 | 18 (then the budget guard) |
| **incomplete reviews** | 0/30 | 1/30 | **4/18 = 22.2% [9.0, 45.2]** |
| **finder recall, ±3 lines** | **17/20 = 85.0% [64.0, 94.8]** | 17/19 = 89.5% | **11/11 = 100% [74.1, 100]** |
| recall, incomplete counted as a miss | 17/20 | 17/20 | 11/12 = 91.7% [64.6, 98.5] |
| recall at ±0 / ±10 lines | 15 / 19 | 16 / 18 | 11 / 11 |
| hits that name the seeded defect (hand read) | 17/20 | 17/19 | **10/11** |
| **verifier keeps, of hit findings** | 20/20 | 19/19 | 17/18 = 94.4% |
| end-to-end recall (a hit that was kept) | 17/20 | 17/19 | 11/11 |
| clean diffs: findings before → after the verifier | 8 → 8 (10 diffs) | 14 → 12 | 8 → 8 (3 diffs) |
| clean diffs with any finding, before → after | 6 → 6 | 6 → 5 | 3 → 3 |
| verifier drops, of clean findings | 0/8 | 2/14 | 0/7 |
| other anchored findings per seeded diff (truth unknown) | 0.3 | 0.4 | 1.3 |
| cost | US$ 0.036 | US$ 0.037 | US$ 0.966 |
| finder call, median | 52 s | 51 s | 130 s |

- **Pooled verifier keep rate** over every judged hit finding: **56/57 = 98.2% [90.7, 99.7]**. By arm: D 39/39, G 17/18.
- **Paired D₁ against G,** on the 11 seeded items where both completed: only G hit 3 (4f9ec26d, 4371af4a, 8ac471ce), only D hit 0. Exact McNemar p = 0.25, so **unresolved**.
- **Floor:** D₁ and D₂ disagree about a hit on 1 of 19 seeded items (4371af4a).

## Decision, by the amended rule

- **The verifier stays on by default, as shipped.** Pooled, it keeps 98.2% of hit findings, above the 90% bar. Each arm has at least 8 judged hits and each is above the 80% floor: D 100%, G 94.4%.
- The uninformative clause does not fire: D₁ completed all 20 seeded items.
- **The experimental label stays,** as registered.
- **The family rule stays,** as registered. This set cannot measure what the rule is for (below). What it did measure is its price: on this set, the reviewer the rule picks costs **about 44× as much** per turn (US$ 0.054 against US$ 0.0012), takes 2.5× as long, and **leaves one review in five incomplete**.

## Predictions

| | predicted | measured | |
|---|---|---|---|
| P1 | G recall ≥ 50%; D 40–75% | G 100% (91.7% with incomplete as a miss); D₁ 85% | G inside; **D above** |
| P2 | verifier keeps ≥ 90% per arm | D 100%, G 94.4% | inside |
| P3 | verifier drops ≤ 25% of clean findings | 0%, 14.3%, 0% | inside |
| P4 | ≥ half of clean diffs get a finding, per arm | 6/10, 6/10, 3/3 | inside |

D's miss was high, the same direction as every missed prediction in `bench/review_judge`: I underestimated the default model.

## What the hand read found

- **The one hit that is not the defect.** On 397cb65e, G's finding sits exactly on the seeded line but reports a doubled period in the message, not the dropped launch error. A location matcher counts it; a reader would not. D found the dropped error there in both replicas.
- **Two borderline hits,** both counted as naming the defect:
  - On d8662bd6, D₂ reports that `alpha * decisions` can exceed 1 and crash `inv_cdf`. That is a real consequence of the seeded operator, but it does not say the correction is inverted.
  - On 2b4354d0, both D replicas cite line 161/162 for a seed at 159. That is within tolerance, and the text names the wrong constant.
- **The one hit finding the verifier dropped was not about the seed.** On 4371af4a, G's second finding, one line from the seed, claimed a two-name unpack crashes on some playwright versions. The verifier dropped it with a specific counter-fact. Every kept-or-dropped verdict on a finding that *does* describe the seed was keep.
- **A "clean" diff was not clean.** On 924aaa76, D₂ and G flagged, independently, that the allowlist computes `escapes` from the list rather than from the tools the registry keeps. That is true on `main` today (`chimera/governance/allowlist.py:60`). It is filed as a separate task. D₂'s verifier dropped its copy of the finding with a false reason ("the diff removes the audit.record block"; the diff moves it). So the precision proxy counts at least two correct findings as false, and the verifier's two drops on clean diffs include one wrong drop.

## What the runs found in the product and the bench

- **Product defect, fixed (`3f5e5936`).** A finder quoting a regex (`\Z`) inside a JSON string made the whole reply unreadable. The review read as `incomplete`, and two correct findings were lost. Found by run 1.
- **The default reviewer runs away.** G's finder hit the 32,000-token completion ceiling on 7 of 31 turns across both runs (`finish=length`, empty content, 130–260 s, about US$ 0.08 each). D did it once in 86 turns. The product reports these reviews as `incomplete`, never as "no findings", which is the behaviour the design exists for. But it means the reviewer the command picks by default, for the default configuration, fails one review in five on diffs of 2–10k characters. Choosing a reviewer rung that does not run away, or bounding the finder's reasoning, is a separate change that needs its own measurement; this run does not make it.
- **Bench defects, caught before any outcome was read (Amendment 1).** The shuffle shared its seed with the clean/seeded draw, so the ten clean items sat at the end of the order. The stop rule also counted the product's `incomplete` as a harness failure.

## What this cannot show

- **Self-preference, the reason the family rule exists.** No diff here was written by either reviewer model. D against G compares two reviewers; it does not show a reviewer grading its own family.
- **Natural review findings.** Each seed is one line, hand-written and visible from the diff. The recall figures (85–100%) are for that kind of defect, and a real review's misses are mostly of other kinds: omissions, cross-file effects, design. aacr-bench or SWR-Bench, with a validated matcher, would measure those.
- **Precision.** The clean diffs are real commits, and at least one held a real defect. Findings off the seed on seeded diffs are unlabelled.
- **The verifier's filtering power.** It dropped 2 of 29 findings on clean diffs, and one of the two drops was wrong. This set shows it does not destroy true catches. It cannot show that it removes false ones, which is what `bench/review_judge` measured at 17% for arm A's stance.
- **G's full-set figures.** G ran 18 of 30 items before its budget guard, and 4 of those were incomplete: 11 seeded and 3 clean reviews completed.
- **Other models, languages, diff sizes, days or providers.**
