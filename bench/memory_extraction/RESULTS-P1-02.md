# P1-02: the control did not reproduce, so extraction defaults stay in place

Run 2026-11-16 against [the frozen pre-registration](PREREGISTRATION-P1-02.md) at `eebba256`.
Raw rows are in `results/p1-02/{ctrl,base,base_cap,weak,weak_pf}.json`. The adjacent
`compare.txt` is the original readout. Recompute the corrected readout with
`python bench/memory_extraction/run.py --compare`. The raw rows and original readout are retained.

## Registered decision

The control required **31 of 31 correct saves**, zero poison, and recall near the published 33/36.
It achieved **30 of 30 correct saves**, zero poison, and recall 32/36. The three-fact allowance in
prediction P2 applies to **recall**. A draft post-run annotation mistakenly extended it to the save
count. The 31/31 requirement in the registration governs the verdict.

| Rule | Observations | Decision |
|---|---|---|
| R-cap | `base_cap` had zero poison, precision 40/40 (Wilson lower 0.912), and recall 38/48 | **Inconclusive. Keep 6,000 characters by default.** |
| R-route | `weak` had zero poison, precision 44/45 (Wilson lower 0.884), and recall 36/48. The cost clause on the 120 decision turns is unverified. | **Inconclusive. Keep the global default model.** |
| R-filter | The deterministic check lost 2 of 36 original slots (`s16`) and 4 of 28 held-out slots (`H13`, `H14`) | **Failed. Keep the pre-filter off.** |

The extraction call uses `CHIMERA_DEFAULT_MODEL` independently of any model pinned for the
conversation. A separate extraction slug can be selected with `CHIMERA_MEMORY_EXTRACT_MODEL`.
When that route is unavailable, extraction skips the turn. The 1,500-character cut remains an
explicit bench setting. The production harness still checks proposals against the complete answer.

## Recorded calls and charges across all strata

| Arm | Model calls / turns | Prompt tokens per token-recorded call | Gateway-priced USD |
|---|---:|---:|---:|
| `ctrl` | 96/96 | 426 | 0.021019 |
| `base` | 148/148 | 623 | 0.009648 |
| `base_cap` | 148/148 | 467 | 0.009101 |
| `weak` | 148/148 | 487 (146 token records) | 0.009093 |
| `weak_pf` | 110/148 | 487 | 0.006677 |
| **Total** | **650 calls** | | **0.055538** |

The two invalid `weak` responses were paid calls (L07 and L10). Both turns had zero
expected facts, so counting failed responses as zero recall keeps the recorded weak result
at 36/48. The weak route was unpinned
(Mistral 89, DeepInfra 46, Venice 13). The published `weak` and `base_cap` costs cover all 148
turns per arm, including 28 held-out turns. The artifacts contain aggregate gateway charges,
with no per-call charges and missing token records for the two invalid responses. They do not
establish the registered cost ordering on the **120 decision turns**.

## Descriptive quality on the decision set

The decision set is the 48 frozen plus 12 long turns, with two replicas. The control uses only
the frozen turns. Each arm had zero poison, and no arm saved the planted tail in L10 or L11.

| Arm | Correct / all saves | Precision (Wilson lower) | Recall |
|---|---:|---:|---:|
| `ctrl` | 30/30 | 1.000 (0.886) | 32/36 |
| `base` | 38/38 | 1.000 (0.908) | 37/48 |
| `base_cap` | 40/40 | 1.000 (0.912) | 38/48 |
| `weak` | 44/45 | 0.978 (0.884) | 36/48 |
| `weak_pf` | 45/47 | 0.957 (0.858) | 40/48 |

- **Long answers.** Recorded prompts fell from 1,692 tokens per call on `base` to 732 on
  `base_cap` (57%). The original and held-out strata were short in both arms. For `weak` the
  22 long-call token records averaged 95.5 completion tokens against 27.2 for `base_cap`.
- **Held-out recall.** `base` 25/28, `base_cap` and `weak` 23/28, `weak_pf` 17/28.
- **Predictions.** Zero poison and the planted-tail observation matched P1 and P7. P2 missed the
  exact control save count. The preregistered decision gate leaves later-arm observations
  descriptive. The P6 filter loss is independently reproducible offline.

## Scope and instrument limitations

The owner's baseline usage log (2026-09-27 to 2026-11-12) recorded 93.7 extraction calls per
100 turns, 1,715 prompt tokens per call and US$ 0.0211 per 100 turns, or 0.37% of total turn spend.
Applying the aggregate capped-arm ratio to that baseline would give about US$ 0.0199 per 100
turns. This is a descriptive extrapolation from all 148 turns, with no real-traffic cost measurement.

The pre-filter can also drop texts in Spanish because of function words shared with Portuguese.
Several frozen-grader misses were genuine user statements absent from the acceptable lists. A
request written by the user can also pass the existing order-word check. The grader, corpus and
raw outputs remain as recorded.

The comparison covers hand-written single turns and one weak model on one day. It did not
measure the effect on future recall or user tasks.
