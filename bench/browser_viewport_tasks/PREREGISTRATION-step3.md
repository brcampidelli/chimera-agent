# M7 step 3 — viewport-first again, with the fixed loop breaker. Pre-registration

*2026-09-24, written before any step-3 solve. Cap **US$ 2**.*

## Why

Step 2 (`RESULTS.md`) lost 0.125 success, with the CI wholly below zero. Every failure it added was the old breaker stopping five `scroll` calls, each of which had returned a new viewport. So the listing's own effect was never measured.

Commit 9c92637 fixed the breaker: a repeat must now match the call and the answer. This step measures the same bundle with that breaker in **both** arms.

## Design

**What stays the same as step 2:**
- the 24 tasks and the pages, with the manifest hash unchanged;
- the model, `deepseek-v4-flash-0731`;
- k = 5 and seed 20260924;
- `solve_one.py`;
- `read.py` and its decision rule.

**The ruler.** A worktree at this branch's commit: current `main`, plus the breaker fix, plus a results-folder switch.

**Results.** `M7_RESULTS=results-step3`, so no step-2 record is mistaken for a step-3 one. Step 2's `results/` is left untouched.

**Stop rules.** Step 2's, with the cap at US$ 2. Step 2 spent US$ 0.34 on the same design.

## Outcomes

`read.py`'s registered read, unchanged. Beside it:
- the `stopped_reason` counts per arm;
- for any `tool_loop` ending, the calls that led to it.

## Predictions

- **The breaker.** No viewport-first solve ends in `tool_loop` after a run of scrolls that each showed something new. Any `tool_loop` that does occur is listed with its calls.
- **Tokens.** Down again, with a ratio of 0.5 to 0.7.
- **Success.** Viewport-first falls within −0.05 to +0.02 of today, and its CI includes zero.
- **Hurt-prone stratum.** Between −0.20 and +0.05. Step 2's −0.60 there was the breaker.
- **Steps.** Viewport-first takes more steps, +0.5 to +3, because it scrolls instead of reading the whole list.

## Decision rule

`read.py`'s rule, as registered for step 2.
- **Adopt as the default** (in its own PR) only if all of its five conditions hold.
- **Otherwise it stays opt-in.**
- **If it loses again with no `tool_loop` endings**, the loss belongs to the listing, and it is recorded as such.

## What this cannot show

Step 2's list stands: real sites, which of the three changes did it, other models, and other viewports.
