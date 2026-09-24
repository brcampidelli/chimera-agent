# M7 step 2 — the element list today against viewport-first, on browsing tasks: results

*2026-09-24 · **US$ 0.34** of the US$ 8 cap · 240 of 240 solves (24 tasks × 2 arms × k = 5), 0 missing · `deepseek-v4-flash-0731` · `PREREGISTRATION.md`, the flag, the suite and the runner committed and pushed before any solve (d5dbce6). Every one of the 240 records names the same ruler: that commit, the worktree's Chimera, and one hash each for `browser.py`, the pages and the tasks · `python -m bench.browser_viewport_tasks.read` reprints it.*

## Verdict: stays opt-in, and the loss is recorded — but every failure it added is the loop breaker stopping a legitimate scroll

**The registered read:**

| | today | viewport-first | viewport-first vs today |
|---|---:|---:|---|
| **task success** | 0.958 | 0.833 | **−0.125** [−0.258, −0.008] |
| prompt tokens per solve (ratio of means) | | | **0.578** [0.429, 0.783] |
| uncached prompt tokens | | | 0.508 [0.385, 0.676] |
| receipt US$ per solve | | | 0.607 [0.453, 0.820] |
| steps per solve | | | +1.03 [+0.59, +1.51] |

**By stratum:**

| stratum | n | today | viewport-first | difference |
|---|---:|---:|---:|---:|
| target in view | 8 | 1.000 | 1.000 | 0.000 |
| below the fold | 16 | 0.938 | 0.750 | −0.188 |
| hurt-prone, registered in advance | 5 | 0.800 | 0.200 | **−0.600** |

**By the rule: stays opt-in.** It fails criteria 1, 2 and 5. The success CI lies wholly below zero, so the registration's loss clause applies: the loss is recorded in the setting's comment and in the tool's docstring. Removing the flag is the owner's call.

## What the failures actually are (§2e — read before the number is interpreted)

**The 20 failures viewport-first added all ended the same way:** `stopped_reason = "tool_loop"`.
- Every one of them had called `scroll` at least 5 times.
- Every viewport-first solve with 5 or more scrolls failed.
- **Today's arm had no `tool_loop` endings at all.** Its 5 failures are all S3, where the target is one of 48 identical "Add to cart" buttons. It picked the wrong one, and S3 fails the same way in both arms.

**The mechanism is in `chimera/core/tool_loop.py`.** `_identical_repeat` breaks the run at **5 calls with identical arguments, whatever they returned**. Five `browser(action="scroll", direction="down")` are identical calls. Each one returns a different viewport, so it is progress, yet the breaker reads it as a spinning loop. It then asks for an answer with no tools. In 11 of the 20 runs, the model answered by writing the tool call it still wanted to make as text (`<｜DSML｜tool_calls>…`).

**So the measured loss belongs to the bundle, as registered, and the bundle includes the breaker.** What this run does **not** show is whether the shorter listing makes targets harder to reach: no failure came from the model giving up or choosing wrong in the viewport arm. The dry-run showed a scripted path reaching every target in both arms.

**The predictions:**

| prediction | outcome |
|---|---|
| tokens to 0.4–0.7 of today's | **confirmed**: 0.578 |
| mean of per-task ratios near 0.8–1.0 | **confirmed**: 0.778 |
| success within −0.05 to +0.02, CI including zero | **refuted**: −0.125, CI below zero |
| in view equal; below within −0.05; hurt-prone −0.10 to −0.30 | in view **confirmed**; below **refuted** (−0.188); hurt-prone **refuted, worse** (−0.600) |
| steps +0.5 to +2 | **confirmed**: +1.03 |
| today solves ≥ 80% | **confirmed**: 95.8% |

## What follows (not run here)

**1. The breaker's identical-repeat rule should look at what came back.** A repeat whose observation changes every time is not a spin. This is a behaviour change to a guard that stops 6.4% of solves in the stored benches (`bench/study24_counts`). It needs its own PR, with a test for a productive repeat and one for a real spin.

**2. Then viewport-first can be measured for what it is.** The same suite, re-registered, with the fixed breaker in both arms. Until then this run says the bundle loses, and why. It does not say the listing loses.

## What this cannot show

The registered list stands: real sites, which of the three changes did it, other models, other viewports.

This outcome adds one more limit: **how viewport-first does when scrolling is not cut short.** Every failure came from that cut.
