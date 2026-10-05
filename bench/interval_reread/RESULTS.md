# Results — 56 published intervals re-read under PROTOCOL §11: two cross, one is already retracted

**2026-10-05**, against [`PREREGISTRATION.md`](PREREGISTRATION.md) (committed before the reader
existed). Reader: [`reread.py`](reread.py); full output [`results/reread.txt`](results/reread.txt),
machine record [`results/reread.json`](results/reread.json),
pinned by `tests/test_the_interval_reread_reproduces_before_it_reads.py`. US$ 0, no model calls.

## Verdict

**56 intervals from 14 benches re-read; all 56 reproduced first; 2 cross their criterion.**

| crosses | published | re-read | what happens |
|---|---|---|---|
| `harness_bench`, checklist × difficulty-tercile interaction, 95% | +0.059 **[+0.002, +0.122]** (bootstrap, 8 and 7 tasks) | **[−0.016, +0.135]** (Welch t) | **correction published** in `bench/harness_bench/RESULTS.md`: "row 3 fires by the letter" is withdrawn |
| `learning_lift` run 6, later family members (n = 60, 4 discordant pairs to 0) | +6.7% **[+0.1%, +6.7%]** "SIGNIFICANT" (old `paired.py`) | **[−1.1%, +14.0%]**, exact McNemar p = 0.125 (Bonett-Price) | already **retracted by run 7** for failing to replicate; per the registered rule the re-read adds nothing to that file |

The second row deserves its sentence here, because it is the defect in `chimera/eval/paired.py`
caught in the act. Run 6's only significant result was four discordant pairs to none — the table
the exact test puts at p = 0.125 — and its printed interval has its upper bound **equal to its point
estimate** (+6.7%, +6.7%), the signature of an interval that conditioned the discordant share away.
Run 7 retracted the claim as "a small-sample fluctuation"; the re-read says the instrument could not
have called it significant in the first place.

## Predictions

| | prediction | outcome |
|---|---|---|
| P1 | the tercile checklist interaction's lower bound falls below zero | **held** — [−0.016, +0.135] |
| P2 | main effects and the other five interactions still span zero | **held** — all 21 stratum and interaction intervals span zero |
| P3 | `chat_history` non-inferiority still not shown | **held** — t [−0.104, +0.026], lower bound under −0.10 |
| P4 | `review_judge` C − A in J out of sample still excludes zero; ΔTPR/ΔFPR widen on the pinned side | **held** — ΔJ [+1.0, +17.6] pp; ΔTPR [+41.6, +45.6] → [+37.8, +52.5] |
| P5 | at least one `significant: true` summary reads not significant | **held** — exactly one, run 6 above |
| P6 | `edit_tools` edit-calls interval still excludes zero | **held** — [−3, −1] by order statistics |
| P7 | no wake/stop verdict rests on its AUROC interval | **held** — wake AUROC 0.796 [0.643, 0.949] Hanley-McNeil; stop Δ = 0 by construction |
| P8 | `spoken_standard` speakable still spans zero, format-speakable still excludes it | **held** — [−20.1, +0.9] and [−34.3, −11.7] pp |

## What moved without crossing

- **`review_judge`.** The conditional interval pinned ΔTPR's upper bound to the point estimate
  whenever one arm won no discordant pair: out of sample +45.6 pp [+41.6, **+45.6**]. Bonett-Price
  gives [+37.8, +52.5]. ΔFPR [+33.9, +36.8] → [+31.9, +39.7]. ΔJ, which no registration had a paired
  method for and was bootstrapped, is [+1.0, +17.6] by MOVER over the two Bonett-Price intervals
  against the published [+1.7, +17.8]; in sample [−8.1, +32.7] against [−6.4, +31.8]; the in-vs-out
  difference [−19.3, +24.8] against [−17.6, +24.0]. Every conclusion stands; every interval is wider.
- **The zero-width intervals.** `memory_graph`'s four slices where the two arms agreed on all 120
  items printed `[0.0, 0.0]`; they read ±2.3 pp now — agreement on 120 items is evidence of a small
  difference, not of none.
- **The other 17 paired summaries** widen by 1.4–78 pp in total width, the largest on the smallest tables
  (`hierarchy_multistep`, n = 3: [−0.20, +0.33] → [−0.46, +0.86]); none changes sign.
- **`chat_history`'s paired line** (Newcombe as H7 printed it, without the phi correction)
  [−10.3, +2.2] → [−10.5, +2.4] with method 10; **`sharded_recap`'s pilot gate** unchanged at
  [−12.5, +19.0] (no concordant failures, so the correction is zero); **`spoken_standard`** as in P8.
- **`edit_tools` tokens**: [−85.5, +70.0] → [−101, +75]; still spans zero, as published.

## Deviations, recorded

- **Two publications of one verdict disagree by 0.001.** `harness_bench/RESULTS.md` prints the
  planner main effect's lower bound as −0.022; `DEAD-TASKS.md` prints −0.023 for the same bootstrap.
  The data reproduce −0.023. The re-read was read against `DEAD-TASKS.md`'s figure and the
  discrepancy is noted in the correction. Nothing turns on it — the interval spans zero either way.
- **`harness_bench`'s family partition** needs each task's `task.yaml: class`, which lives in the
  external benchmark checkout (`~/harness-bench/tasks/`), not in this repository. The seven non-SE
  tasks were read from there on 2026-10-05 and are written into `reread.py`; the reproduction of the
  published family-partition bootstraps to the digit is what shows the copy is right.
- **No DeLong.** AUROC intervals use Hanley-McNeil, which needs only the AUC and the class sizes and so
  can be re-read from a summary. Neither AUROC verdict depended on its interval.

## What this does not show

That the retired methods were wrong about anything else. A wider interval that still clears its
criterion confirms the verdict; the re-read changes two verdicts out of 56 intervals and both sat
within 0.01 of their line. What it shows is that the two near-threshold claims the project carried —
a "significant" transfer in run 6 and a "firing" interaction in the harness factorial — were both
products of the instrument.
