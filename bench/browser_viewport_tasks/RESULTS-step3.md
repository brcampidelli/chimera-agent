# M7 step 3 — viewport-first with the fixed loop breaker: results

*2026-09-24 · US$ 0.38 by receipt (0.95 charged at the dearest seen rate, of the US$ 2 cap) · 240 of 240 solves, 0 missing · `deepseek-v4-flash-0731` · `PREREGISTRATION-step3.md` committed and pushed before any step-3 solve (c453334) · every record names one ruler: that commit, the fixed-breaker Chimera, and step 2's page and task hashes · `M7_RESULTS=results-step3 python -m bench.browser_viewport_tasks.read` reprints it.*

## Verdict: stays opt-in — with the breaker fixed, the loss falls from −0.125 to −0.042, and what is left belongs to the listing

| | step 2 (old breaker) | **step 3 (fixed breaker)** |
|---|---:|---:|
| success, today → viewport-first | 0.958 → 0.833 | 0.958 → **0.917** |
| Δ success [95% CI] | −0.125 [−0.258, −0.008] | **−0.042** [−0.083, −0.008] |
| prompt tokens (ratio of means) | 0.578 | 0.760 [0.500, 1.030] |
| uncached prompt tokens | 0.508 | 0.417 [0.301, 0.593] |
| steps per solve | +1.03 | +1.70 |
| stratum: in view | 0.000 | −0.025 |
| stratum: below the fold | −0.188 | −0.050 |
| stratum: hurt-prone | −0.600 | −0.120 |
| viewport-first runs ended by the breaker | 20 | **1** |

**By the rule: stays opt-in.** Two conditions fail:
- the success CI still lies wholly below zero, though only just;
- the prompt-token ratio's CI now reaches 1.0.

## What the remaining failures are

**Today's arm** fails only S3, five times. The target there is one of 48 identical "Add to cart" buttons. S3 fails the same way in the viewport-first arm, so it contributes nothing to the difference.

**Viewport-first's five other failures:**
- **G2 r4 — the one breaker stop, and a real loop.** Four calls with an empty, malformed action, each with the same result. The fixed rule still catches this, as it should.
- **W3 r1 — the step limit.** Fifteen scrolls looking for "the third book under Further reading", a target defined by position. The listing does not show where it is.
- **F1 r1, R3 r0, W3 r2 — wrong answers.** The model picked the wrong element and reported its code.

**So the step-2 loss was mostly the breaker,** as that report said. Step 3 measures what the listing itself costs: about four points of success, concentrated where the target is defined by its place on the page. And it no longer saves prompt tokens at a level the CI can distinguish from none, because the model scrolls instead of reading the full list. The uncached saving is still clear.

## Predictions against the outcome

| prediction | outcome |
|---|---|
| no viewport-first `tool_loop` after a run of productive scrolls | **confirmed**: the one `tool_loop` is a loop of malformed calls |
| tokens down, ratio 0.5–0.7 | **refuted**: 0.760, CI reaches 1.0 (uncached 0.417) |
| success within −0.05 to +0.02, CI including zero | **half**: −0.042 is inside the band; the CI excludes zero |
| hurt-prone between −0.20 and +0.05 | **confirmed**: −0.120 |
| more steps, +0.5 to +3 | **confirmed**: +1.70 |

## Apparatus

- **First launch halted after 4 solves on the cap guard.** Four solves in flight reserve 4 × US$ 0.5625, which exceeds a US$ 2 cap. The run was relaunched at concurrency 2. The cap was unchanged, and the four finished solves were kept: the halt had nothing to do with their outcomes.
- **One solve (W1 today r3) was cut by the chunk's time limit** and re-ran whole in a short follow-up.

## What this cannot show

Step 2's list stands: real sites, which of the three changes did it, other models, other viewports.

## After this result (2026-09-24)

The owner removed the setting. `CHIMERA_BROWSER_VIEWPORT_FIRST` no longer exists, so the assembled browser always lists the whole page. The mode is still `BrowserTool(viewport_first=True)`, which is what `harness.py` constructs, so both steps reproduce from main. The only difference is `browser.py`'s hash in the `ruler` field: the edit that removed the setting also rewrote a comment in that file.
