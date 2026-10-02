# Pre-registration — a cut on the finder's confidence in `chimera review`, and an effort level

**Registered 2026-10-02, before the analysis below was run on any cut.** Study 28, review item R3.
**Budget: US$ 0.** No model is called: this re-reads two run files already committed with their benches.

## Why

`chimera review` asks its finder for every defect with a confidence (`chimera/review/finder.py`), and
`tests/test_the_review_finder_is_never_told_to_narrow.py` keeps the finder's prompt free of any word that narrows it.
The confidence is then used for one thing: ordering findings inside a priority (`report.order`). Nothing filters on it.

The filtering belongs in code, after the finder, where every drop is recorded with its stage and reason. That is the
design of the verifier, and a confidence cut would be a third stage of the same kind. Whether it is worth having
depends on two things a re-read can measure:
- how many of the seeded defects it hides;
- how many findings on clean diffs it removes.

**What was known before this file was written.** Study 28 re-read `bench/review_reviewer` arm D and reported one
figure: at a cut of 0.7, D's shown seeds went from 34/40 to 33/40, and its findings on clean diffs from 9.5 to 3.5 per
ten diffs. I have read that sentence, and while reading the file format I saw a handful of confidence values on single
records (0.8, 0.9, 0.95). I have not computed anything at any cut, for any arm, on either set. The 0.7 figure for
arm D is part of the selection set below, so the selection set is not blind. That is why a second set confirms.

## Data

| set | file | cells | what it was |
|---|---|---|---|
| **selection** | `bench/review_reviewer/results/run.json` | D, L, Q, both replicas | product `e44ca6c0`, 2026-09-26 |
| **confirmation** | `bench/review_seeded/results/run.json` | D1, D2, G | product `3f5e5936`, 2026-09-25, another session |

- **Same items, other runs.** Both use the same fixtures (`b2f9e29a5fd3`) and prompts (`3a480dab0478`, `ee9d780a9f4b`),
  so the confirmation set is not new diffs. It is new finder replies, on another day, including a model (G, glm-5.3)
  that the selection set does not have. It confirms that the cut transfers across sessions and one more model, not
  across code.
- **Cells.** M is left out: it was not measured (`bench/review_reviewer/RESULTS.md`). L is the product's default
  reviewer and D its second entry (`chimera/review/family.py`, `MEASURED_REVIEWERS`); Q adds a third family.
- **Which reviews count.** The completed reviews, as each source bench counts them: review_reviewer's `_done` and
  review_seeded's `_cell`.

## What a cut does

A finding is **shown** when the verifier did not drop it, and its confidence is at least the cut.
- **A finding with no confidence is never cut.** An unknown is not a low, as an unread verdict is not a drop.
- **The verifier judged one finding per call,** so cutting before or after it shows the same set. The product will cut
  before, which also spares the verifier's calls on findings it would hide anyway.

Two counts, both from the stored records:
- **Seeds shown:** seeded reviews where a hit (seeded file, line ±3) is shown. On a seeded diff the stored verdicts
  cover only the hits (each source bench's registered subset), so this is exactly the recall each bench published.
- **Clean findings shown:** findings shown on the clean diffs. Every anchored finding on a clean diff was verified.

**Positive control (§2aa), run before this file was committed:** with no cut, `analyse.py --check` reproduces every
published figure: selection D 34/40 and 19 clean findings, L 39/40 and 17, Q 27/38 and 25 (13.0 per ten, per
replica); confirmation D1 17/20 and 8, D2 17/19 and 12, G 11/11 and 8. **Reproduced.**

## The grid

0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9. A cut c shows a finding when `confidence >= c`.

## The frozen rule

**Selection.** A cut passes when, on the selection set:
1. it keeps at least **95%** of the seeds shown with no cut, pooled over D, L and Q;
2. it keeps at least **90%** of each arm's own seeds shown;
3. it removes at least **50%** of the clean findings shown with no cut, pooled.

Among the cuts that pass, the chosen one removes the most clean findings; a tie goes to the lower cut.

**Confirmation.** The chosen cut is confirmed when, on the confirmation set, pooled over D1, D2 and G, it keeps at
least **90%** of the seeds shown, and removes at least **30%** of the clean findings shown. The bars are lower because
that set is smaller (50 seeded reviews with 45 seeds shown, 23 clean reviews) and played no part in choosing.

**Why these bars.** `bench/review_seeded` kept its verifier on because it kept 98% of the true catches, against a bar
of 90%: "a filter that drops more than one true catch in ten costs more than the noise it removes." A cut is a
cruder filter than a verifier (it reads one number the finder wrote about itself), so the bar on true catches is
stricter here, 95%. The 50% on clean findings says a cut has to remove most of the noise to be worth a default.

**What ships, by outcome** (the shape is fixed now, so the numbers cannot choose it):

`chimera review` gets `--effort low|medium|high`:
- **low:** the finder alone, no verifier, no cut. That is today's `--no-verify`, which stays as its alias.
- **medium:** the finder, the cut, then the verifier on what the cut keeps.
- **high:** the finder and the verifier, with no cut. That is today's behaviour.

| outcome | medium's cut | default effort |
|---|---|---|
| a cut passes selection, and is confirmed | the chosen cut | **medium** |
| a cut passes selection, not confirmed | the chosen cut | high (today's) |
| no cut passes all three; some pass 1 and 2 | of those, the one removing the most clean findings | high |
| no cut passes 1 and 2 | none: medium is not offered, only low and high | high |

- The cut is applied **in code**, in the pipeline. The finder's prompt does not change, and its no-narrowing test
  stays as it is.
- A finding the cut hides is not deleted. It goes to the dropped list with its own stage and reason, as the anchor
  and the verifier already do, and `--show-dropped` prints it.

## Predictions

- **P1.** At 0.7 the selection set passes 1 and 2 (D is known to keep 33/34).
- **P2.** At least one cut passes all three on the selection set, and the chosen cut is 0.7 or 0.75.
- **P3.** The chosen cut is confirmed.
- **P4.** L, which writes the fewest findings, loses the fewest clean findings to the cut in proportion.

## Reported, not decided on

- The confidence histogram of the hit findings shown and of the clean findings shown, per set, and how many have no
  confidence.
- Verifier calls spared by cutting first.
- Anchored findings on seeded diffs that miss the seed, before and after the cut. Their truth is unknown.
- **The known-true finding on a "clean" diff.** On 924aaa76 two reviews in `bench/review_seeded` flagged a real defect
  in the allowlist. The proxy counts it as false, so a cut that hides it books a loss as a gain. Whether the chosen
  cut hides it is printed.

## What this cannot show

- **Calibration.** The confidence is a number the finder writes about its own finding. It is not calibrated, and it
  depends on the model: a cut fitted on D, L, Q and G says nothing about another reviewer. `--reviewer-model` and
  `CHIMERA_REVIEW_MODEL` can name any model, and the cut then applies on no measurement.
- **Natural defects, other languages, larger diffs.** Each seed is one line, hand-written and visible from the diff.
  The confidence a finder gives such a defect may be higher than the one it gives a real omission or a design flaw.
- **Precision.** The clean diffs are real commits presumed clean; one was not. Findings off the seed are unlabelled.
- **n.** 118 seeded reviews in the selection set, with 100 seeds shown and no cut (D 34, L 39, Q 27). The pooled 95%
  bar allows five lost seeds of 100. The per-arm 90% bar allows three for D and L, and two for Q.
- **Independence.** Replicas of one item are not independent (ICC 0.706, `bench/review_seeded`), so the pooled
  counts are not 118 independent draws.
