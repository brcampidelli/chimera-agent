# Local decider bake-off — results (2026-10-07)

Pre-registration: [`PREREGISTRATION.md`](PREREGISTRATION.md) (commit 9bb547a8, before any forward
pass) and its Amendment 1 (5402a0b7, before any rerun). Full readout: [`report.md`](report.md).
One laptop (RTX 5070 Laptop, 8 GB), one run per arm, serial GPU queue with nothing else loaded.

## Verdict

| arm | JevBench total (easy / original / hard) | ambiguous AUROC (≥ 0.853) | worst wrapper → ALLOW (≤ 2) | p95 governance | halts | rule |
|---|---|---|---|---:|---:|---|
| **clef-q4** (Clef Flash Q4_K_M, Q8_0 head) | **0.810** (1.000 / 1.000 / 0.604) | **0.864** | 1/22 | 0.24 s | 0 | **eligible to be offered** |
| intern-2b | 0.779 (1.000 / 0.847 / 0.640) | 0.760 | 2/19 | 0.24 s | 0 | not eligible (1, 3) |
| eikos-4b (Q8_0) | 0.840 (1.000 / 0.917 / 0.721) | 0.779 | 1/22 | 0.50 s | 0 | not eligible (1) |

**clef-q4 is eligible to be offered as an optional local decision backend.** Per the rule this
changes nothing in the product: an adoption PR is separate, and it is never the default without
the owner's decision. It runs in 5.6 GB of VRAM at load.

- Control C passes: the same-day hosted Clef Flash on the same 55 rows agrees on danger 54/55 and
  on the verdict 54/55, and the AUROC moves by +0.003 (hosted 0.861). Quantization cost nothing
  measurable on governance.
- The ambiguous AUROC rests on 35 rows (14 attacks, 21 benign). Hanley-McNeil 95% intervals: clef-q4 0.864 [0.729, 0.999], intern-2b 0.760 [0.590, 0.930], eikos-4b 0.779 [0.614, 0.944]. The 0.853 bar lies inside all three, so the rule separates the arms on point estimates, not on evidence that they differ.
- JevBench 187/231 clears the 0.788 bar by 5 items. The Wilson interval is [0.754, 0.855]; the bar
  sits inside it, so this is a pass by the registered rule, not evidence that the true accuracy is
  above 0.788. Hosted Clef reads 0.823 on the same items.
- On JevBench's long prompts (up to 4,032 tokens) the request p95 is **1.53 s**. The latency
  condition is read on governance requests (≤ 450 tokens) by registration; a caller sending long
  contexts will not see 0.24 s.

## Amendment 1 — what the first clef run hid

The first clef-q4 run served with llama.cpp's default physical batch of 512 tokens. In decision
(embedding) mode the whole prompt must fit one batch, so every request above 512 tokens returned
HTTP 500: 68 of 111 hard items, none of the governance prompts (378–450 tokens). Two guards let it
through — a halt during the smoke did not abort, and the easy-items guard passed because easy items
are short. That run is kept as `results/clef-q4/jevbench.ub512.jsonl` and never read.

The rerun used `-b/-ub 8192` and redid all 231 items. Both reuse checks passed: the 120 easy and
original items repeat with the same choice and |Δp| = 0.000, and the 55 unwrapped governance rows
repeat their verdict with |Δp| = 0.000, so the governance rows stand. The two guards are now
abort-on-halt for every arm (exit 7).

## Predictions

| | prediction | reading | |
|---|---|---|---|
| P1 | control C passes (danger ≥ 0.90) | 54/55 | held |
| P2 | clef AUROC within ±0.03 of 0.861 | 0.864 | held |
| P3 | clef worst wrapper 2/22 ± 1 | 1/22 | held |
| P4 | clef JevBench within ±0.03 of 0.823 | 0.810 | held |
| P5 | clef p95 0.2–0.6 s | 0.24 s | held |
| P6 | intern-2b reproduces its card, fails condition 3 narrowly | 1.000 / 0.847 / 0.640, 0.779 | held |
| P7 | intern-2b AUROC below 0.835 | 0.760 | held |
| P8 | intern-2b p95 ≤ 0.3 s | 0.24 s | held |
| P9 | eikos reproduces its card, passes condition 3 | 0.917 / 0.721, 0.840 | held |
| P10 | eikos AUROC 0.78–0.85, fails condition 1 | 0.779 | **missed the range by 0.001** (fails condition 1 as predicted) |
| P11 | eikos flips > 2 on some wrapper | 1/22 | **refuted** |
| P12 | eikos p95 ≤ 0.5 s | 0.498 s | held |
| P13 | top-50 fallback on < 1% of rows | 0 rows | held |

## Defects found in the apparatus

- The 512-token batch above, caught by an implausible hard score (0.279 with 68 halts).
- The "other GPU process" guard flagged a `python.exe` on intern-2b. It was the arm's own sidecar:
  uv's venv `python.exe` is a launcher that starts the real interpreter under another PID, and the
  guard compared the launcher's. No other model was on the GPU.

## What this cannot show

One run per arm, one machine, 35 ambiguous governance rows (the AUROC interval is wide), JevBench
items that may sit in the vendors' training data, and no concurrency.
