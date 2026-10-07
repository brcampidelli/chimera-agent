## JevBench, 231 items (unread or failed = wrong)

| tier | Intern-Decision-4B (ours, HF + offload) | vendor (XTuner) | inside our interval? |
|---|---|---:|---|
| easy | 48/48 = 1.000 [0.926, 1.000] | 1.0 | yes |
| original | 71/72 = 0.986 [0.925, 0.998] | 0.9861 | yes |
| hard | 82/111 = 0.739 [0.650, 0.811] | 0.7387 | yes |
| all | 201/231 = 0.870 [0.821, 0.907] | — | — |

Halts: 0. Control (easy ≥ 0.90): met.

## Calibration (top-label ECE, 10 bins; multiclass Brier)

| reading | tier | ECE | Brier |
|---|---|---:|---:|
| raw, T = 1 | hard | 0.134 | 0.369 |
| raw, T = 1 | all | 0.060 | 0.233 |
| vendor preset T = 1.9924 (borrowed, XTuner-fitted) | hard | 0.061 | 0.332 |
| vendor preset T = 1.9924 (borrowed, XTuner-fitted) | all | 0.050 | 0.217 |

## Paired against our local decider (same 231 items)

**vs qwen3:4b shipped rendering (2026-09, `jevbench_local`)** — reference 143/231 = 0.619
- easy: Intern 48 vs reference 48 · n = 48 · Intern-only 0 / reference-only 0 · exact p = 1
- original: Intern 71 vs reference 59 · n = 72 · Intern-only 13 / reference-only 1 · exact p = 0.0018
- hard: Intern 82 vs reference 36 · n = 111 · Intern-only 50 / reference-only 4 · exact p = 3.8e-11
- all: n = 231 · Intern-only 63 / reference-only 5 · exact p = 7.7e-14

**vs qwen3:4b digit ids (S30-54 `C_numeric`)** — reference 159/231 = 0.688
- easy: Intern 48 vs reference 46 · n = 48 · Intern-only 2 / reference-only 0 · exact p = 0.5
- original: Intern 71 vs reference 55 · n = 72 · Intern-only 16 / reference-only 0 · exact p = 3.1e-05
- hard: Intern 82 vs reference 58 · n = 111 · Intern-only 34 / reference-only 10 · exact p = 0.00039
- all: n = 231 · Intern-only 52 / reference-only 10 · exact p = 5.7e-08

Latency (server forward, this laptop with CPU offload — not the model's speed): p50 771 ms, p95 2954 ms · input tokens median 245, max 4074
