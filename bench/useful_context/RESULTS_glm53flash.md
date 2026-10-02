# glm-5.3-flash holds an agent's context to 512k, the top of the ladder

Run 2026-09-30 against [`PREREGISTRATION_large.md`](PREREGISTRATION_large.md) and its amendments, on the
**Novita** route (fp8).

- **Calls:** 458 on this route: 26 pilot and 432 main. None errored, none was mis-routed, none was cached at 512k.
- **Spend on this route:** US$ 4.82 (US$ 0.2217 pilot + US$ 4.5935 main).
- **Spend on the whole study:** **US$ 5.61** recorded, including the Sail Research attempts. The unrecorded
  worst case is ≈ US$ 1.80 more: the abandoned 900k calls, the relaunch's in-flight item and the pilot's
  13-second first launch. That puts the worst case at ≈ US$ 7.41, under the approved US$ 8.
- **Routing:** every row was answered by `Novita` and billed at exactly the quoted price (ratio 1.00).
- **Reproducing the tables:** `python bench/useful_context/run.py --profile glm53flash512_novita --report
  bench/useful_context/results/glm53flash512_novita_main.json`. The output is in
  `results/glm53flash512_novita_main-report.txt`.

## The answer, under the registered rule

| | |
|---|---|
| Useful length | **≥ 512k**: the 512k cell, median **512,041** provider tokens. The top of the ladder, so a **lower bound** |
| Proposed trigger, `floor(0.8 × useful, to 1,000)` | **409,000** provider prompt tokens |
| Today | the model has no `useful_k`, so the budget uses the conservative rule for unmeasured models (64k) |

The predictions stood:

- **P1:** both 4k controls pass. They did.
- **P2-B:** it holds within the margin at 512k, a lower bound of 512. It did.

## Gates

| gate | result |
|---|---|
| grader self-test, corpus collisions | PASS, 0 |
| pilot 4k (≥ 18/20) | **20/20**; the route served 512k in 23–53 s |
| positive control, main 4k (≥ 90%) | **54/54** |
| floor: 4k replay | 0/54 discordant, 54/54 answers byte-identical: **PASS** |
| routing | 0/458 mis-routed |
| cache rule (> 5% of 512k rows over half cached) | not met: 0.00 of 512k tokens were cached |

## Per length (n = 54, the same items at every length)

| length | median tokens | ok | accuracy | Wilson 95% | paired Δ vs 4k [95%] | failure |
|---:|---:|---:|---:|---|---|---|
| 4k | 3,612 | 54 | 1.000 | [0.934, 1.000] | — | — |
| 16k | 15,914 | 53 | 0.981 | [0.902, 0.997] | −0.019 [−0.098, +0.050] | answered "none so far" (M034) |
| 32k | 31,956 | 54 | 1.000 | [0.934, 1.000] | 0.000 [−0.066, +0.066] | — |
| 64k | 63,991 | 54 | 1.000 | [0.934, 1.000] | 0.000 [−0.066, +0.066] | — |
| 128k | 128,117 | 53 | 0.981 | [0.902, 0.997] | −0.019 [−0.098, +0.050] | named a distractor (M000) |
| 256k | 256,038 | 54 | 1.000 | [0.934, 1.000] | 0.000 [−0.066, +0.066] | — |
| **512k** | **512,041** | **53** | **0.981** | [0.902, 0.997] | −0.019 [−0.098, +0.050] | wrote `[habor-vane]` for `[harbor-vane]` (M048) |

- **Every rung's interval sits inside the registered −0.10 margin.** Each of the three misses is a single item,
  one per rung.
- **Latency grows with length:** a median of 6.9 s at 4k, 24.6 s at 256k and 46.1 s at 512k, reasoning included.

## What it took to get here — every attempt is published

1. **Sail Research, the route first registered.** It could not serve 900k: the server cuts at ~300 s before
   the first byte, streamed or not (options A and C, retracted readings included).
2. **Sail Research at 512k (option B).** Upstream 429s stopped the run twice, at three calls and at one.
   Stopped files: `glm53flash512_main.json` and `glm53flash512_slow.json`.
3. **Novita (option 2).** Chosen by the registered rule, the cheapest fp8 endpoint serving 1,048,576 tokens,
   with Sail excluded.

Two runner defects were found on the way and fixed before this run, each with a test watched to fail:

- **Rows lost after the stop rule.** The items in flight when the rule fired were paid for, and their rows
  were dropped. They are now kept as `after_stop_rows`, beside the analysed rows, with the runner's own count
  in `runner_usd`.
- **An unread prompt graded as a wrong answer.** A reply that read no prompt (0 prompt tokens) was scored
  `empty`. It is now retried, and recorded as an error if it persists. That happened once, in the Novita pilot.

## What this cannot show

- **Only this route.** Another host's build of the same weights may read differently. That is the reason for
  the pilot, and the Sail Research route could not even be read.
- **Nothing above 512k.** The model advertises 1,048,576 tokens. This says nothing about the rungs above 512k.
- **Only this task shape.** The limits of [`PREREGISTRATION.md`](PREREGISTRATION.md) apply.
- **Adoption is separate.** Writing `useful_k = 512` into the catalogue is done in a separate PR, as
  registered.
