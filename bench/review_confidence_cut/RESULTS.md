# Results — a cut on the finder's confidence in `chimera review`

2026-10-02. Pre-registered in `PREREGISTRATION.md` (`6cea67d0`). US$ 0: `analyse.py` re-reads the stored run files of
`bench/review_reviewer` (selection) and `bench/review_seeded` (confirmation) and calls no model. `report.txt` is its
own output, and every figure below comes from it.

## Decision

**The default stays `high`, today's behaviour: no cut.** The rule selected **0.8**, and the confirmation set did not
confirm it.
- At 0.8 the confirmation set kept **39 of 45** seeds shown (86.7%), under the 90% bar.
- `--effort medium` ships as an opt-in, with the cut at the selected 0.8, as the frozen table says for this outcome.
- `--effort low` is today's `--no-verify`, which stays as its alias.

## Positive control

With no cut, every figure both source benches published is reproduced: selection D 34/40, L 39/40, Q 27/38 seeds shown,
with 19, 17 and 25 clean findings; confirmation D1 17/20, D2 17/19, G 11/11, with 8, 12 and 8.

Study 28's one figure is reproduced too. At 0.7, D shows 33 of its 34 seeds, and 7 clean findings over 20 diffs, which
is 3.5 per ten.

## Selection: `bench/review_reviewer`, D, L and Q, both replicas

Seeds shown and clean findings shown, after the cut over before. "Checks" are the verifier calls the product makes
when it cuts first.

| cut | seeds kept, pooled | D | L | Q | clean removed, pooled | D | L | Q | checks | passes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 0.5 | 99/100 | 33/34 | 39/39 | 27/27 | 10/61 = 16.4% | 12/19 | 17/17 | 22/25 | 170/182 | |
| 0.6 | 99/100 | 33/34 | 39/39 | 27/27 | 17/61 = 27.9% | 10/19 | 17/17 | 17/25 | 163/182 | |
| 0.7 | 99/100 | 33/34 | 39/39 | 27/27 | 28/61 = 45.9% | 7/19 | 15/17 | 11/25 | 149/182 | no (clean) |
| 0.75 | 98/100 | 32/34 | 39/39 | 27/27 | 35/61 = 57.4% | 4/19 | 14/17 | 8/25 | 137/182 | **yes** |
| **0.8** | **97/100** | 32/34 | 38/39 | 27/27 | **39/61 = 63.9%** | 4/19 | 11/17 | 7/25 | 132/182 | **yes, chosen** |
| 0.85 | 93/100 | 31/34 | 37/39 | 25/27 | 48/61 = 78.7% | 1/19 | 10/17 | 2/25 | 112/182 | no (seeds) |
| 0.9 | 92/100 | 31/34 | 37/39 | 24/27 | 50/61 = 82.0% | 1/19 | 8/17 | 2/25 | 108/182 | no (seeds) |

The per-arm cells show what is left after the cut, over what was there before it.
- 0.75 and 0.8 pass all three conditions. 0.8 removes more clean findings, so the rule chooses it.
- At 0.8 the cut spares over a quarter of the verifier's calls on this set (50 of 182).

## Confirmation: `bench/review_seeded`, D1, D2 and G

| cut | seeds kept, pooled | D1 | D2 | G | clean removed, pooled | D1 | D2 | G |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0.5 | 43/45 = 95.6% | 16/17 | 17/17 | 10/11 | 12/28 = 42.9% | 6/8 | 8/12 | 2/8 |
| 0.6 | 43/45 = 95.6% | 16/17 | 17/17 | 10/11 | 16/28 = 57.1% | 5/8 | 6/12 | 1/8 |
| 0.7 | 41/45 = 91.1% | 15/17 | 17/17 | 9/11 | 20/28 = 71.4% | 2/8 | 5/12 | 1/8 |
| 0.75 | 40/45 = 88.9% | 15/17 | 16/17 | 9/11 | 23/28 = 82.1% | 2/8 | 3/12 | 0/8 |
| **0.8** | **39/45 = 86.7%** | 15/17 | 16/17 | **8/11** | 23/28 = 82.1% | 2/8 | 3/12 | 0/8 |
| 0.85 | 39/45 = 86.7% | 15/17 | 16/17 | 8/11 | 25/28 = 89.3% | 1/8 | 2/12 | 0/8 |
| 0.9 | 38/45 = 84.4% | 15/17 | 16/17 | 7/11 | 26/28 = 92.9% | 1/8 | 1/12 | 0/8 |

At the chosen 0.8 the cut removes 82% of the clean findings, well past the 30% bar, but keeps 86.7% of the seeds,
under the 90% bar. **Not confirmed.**

- **Where the seeds went.** G (glm-5.3) lost 3 of its 11 shown seeds at 0.8, and D1 lost 2 of 17. glm-5.3 writes
  lower confidences on true findings than the selection set's models do. Its true hits include 0.25, 0.3 and 0.4.
- That is the limit the pre-registration named: the cut is a number each model writes about itself, so a value
  fitted on three models did not transfer to a fourth.

**Read after the rule, and not acted on.** 0.7 would have passed the confirmation set (91.1% kept, 71.4% removed). It
failed selection on the clean condition (45.9% removed, against 50%). Choosing it now would mean choosing the cut
after seeing both sets, which is what the two sets exist to prevent.

## Predictions

| | predicted | measured | |
|---|---|---|---|
| P1 | at 0.7 the selection set passes conditions 1 and 2 | 99/100 pooled, D 33/34 | inside |
| P2 | a cut passes all three; the chosen one is 0.7 or 0.75 | 0.75 and 0.8 pass; 0.8 chosen | **wrong value** |
| P3 | the chosen cut is confirmed | 86.7% against a 90% bar | **wrong** |
| P4 | L loses the fewest clean findings in proportion | at 0.8: L 35%, Q 72%, D 79% | inside |

## Reported, not decided on

- **No finding was missing a confidence.** That held for every shown finding in both sets: 113 hit and 61 clean
  findings in the selection set, 57 and 28 in the confirmation set.
- **The confidences are lumpy.** In the selection set, 43 of the 113 hit findings shown say exactly 1.0, and 40 more
  say 0.95 or above. The clean findings spread from 0.3 to 0.98. Every model rounds, and to different grids.
- **Off-seed findings on seeded diffs** (truth unknown) at 0.8: 33 → 17 in the selection set, 27 → 6 in the
  confirmation set.
- **The known-true finding on a "clean" diff** (924aaa76, line 60 of the allowlist: `escapes` is computed from the
  configuration, not from what the registry keeps).
  - With no cut, 7 reviews show it, and one more found it but its verifier dropped it.
  - At 0.8 it stays in 5 of them and is hidden in 2: Q's replica 2 and G, both at confidence 0.7.
  - So the cut does to a real defect what it does to the seeds, and the clean-removal figure counts those 2 as false
    findings removed.
- **One change to `analyse.py` after the pre-registration, in a reported-only section.** It first matched this finding
  by the word "escape" anywhere in the file's findings. That also caught two other findings on lines 90 and 98, which
  are not this defect. It now matches by place, line 60 ±3, as the seeds are matched. No decided figure depends on it.

## The product change

As registered for this outcome:
- `chimera review --effort low|medium|high`, default **high**:
  - **low:** finder only;
  - **medium:** finder, then the cut at 0.8, then the verifier on what the cut keeps;
  - **high:** finder and verifier, no cut.
- `--no-verify` is kept as an alias of `low`.
- The cut is in the pipeline (`chimera/review/pipeline.py`), never in the finder's prompt. A finding under it goes to
  the dropped list with stage `confidence` and its reason, and `--show-dropped` prints it.
- The report says which effort ran (`reviewer.effort`, `reviewer.confidence_cut`).

## What this cannot show

- **Calibration, and other models.** The confirmation set already showed it: one model's 0.8 is another's 0.7. A user
  who picks a reviewer with `--reviewer-model` or `CHIMERA_REVIEW_MODEL` runs `medium` on no measurement.
- **Natural defects.** One-line seeds, visible from the diff, are what a finder is most sure of. Real omissions and
  design flaws may come with lower confidences, and the cut would hide more of them.
- **Precision.** The clean-diff figure is a proxy, and one clean diff held a real defect, as above.
- **New code.** Both sets review the same 30 diffs (fixtures `b2f9e29a5fd3`). The confirmation is across sessions and
  models, not across code.
- **Independence.** Replicas of one item are correlated (ICC 0.706), so 100 pooled seeds are fewer independent draws.
