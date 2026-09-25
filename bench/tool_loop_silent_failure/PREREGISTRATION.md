# The loop breaker and failures the tool reports itself: a replay registration

*2026-09-24 · US$ 0, no solve, only recorded traces · committed and pushed before the replay runs.*

## The miss

The breaker treats "the same failure under different args" as a wall. It only knows a call failed when the loop says so: an `error:` answer or a refusal.

A command that ran and failed looks like a success to the loop. The unregistered post-hoc read in `bench/tool_loop_near_args` (§4 of its RESULTS) found three walls that no rule stopped:
- `run_shell` given its command as a list. It answered `[exit 127] /bin/sh: 1: [bash,: not found` whatever the list held (`brk-legacy-weak` 041 r8, `m6f-weak` 041 r66).
- `code_interpreter` answering `ZeroDivisionError: float division by zero` on four different pieces of code (System One weak 089 r2).

**These three cases are where the rule comes from, not a test of it.** A replay that reproduces them only shows that the rule does what it was written to do.

## The change (this branch, `chimera/core/tool_loop.py`)

**`_answered_failure(observation)` reads a failure from the answer itself.** Two forms count:
- `[exit N]`, with N ≠ 0 and **some text after it**. This is what `run_shell` and `execute_code` answer.
- A last line shaped like an exception, `SomethingError: …` or `…Exception: …`. This is what `code_interpreter` answers.

A non-zero exit with no text does not count: a `grep` or `test` that found nothing is exploring, not a wall.

**How the detector uses it:**
- It records `failed = ok is False or _answered_failure(observation)`.
- `_no_progress` uses `failed` where it used `ok is False`.
- The detector has a new stop reason, `failed the same way N×`. It is used only when every call in the run failed in its answer, not by the loop's `ok`.
- The wording for runs the loop saw fail is unchanged.
- The trace's and the receipt's `ok` are unchanged.

## What the replay can and cannot show

Same structure as `bench/tool_loop_near_args`, with the same populations and helpers.

The new condition is still a subset of the legacy one: same tool, same output, any args. So on legacy traces it can only reproduce stops that legacy already made. Cost can only be seen on the 76 runs of `brk-fixed-*` that continued.

**The baseline is `near`, main after #583, frozen in `detectors/`.** "New-only" means the new rule fires and `near` does not, at that step or earlier.

**One quantity is new:** how often `_answered_failure` reads a failure in a call the loop recorded as a success. This is §2r's "how much it acted".

## Gates

**F.** The fidelity gate is unchanged: legacy replayed on legacy rows, fixed on `brk-fixed-*`, at least 95% agreement in every group. Below that, nothing is read.

## Decision rule

The rule merges as the default only if all four hold:

1. **F passes.**
2. **The new rule reproduces zero legacy stops that `near` does not, where the tail is writes with distinct args.** That is the #577 false alarm.
3. **Zero new-only fires in `brk-fixed-strong`.**
4. **No new-only fire in `brk-fixed-weak` in a run that scored above its (task, weak, fixed) cell median.**

**If 2 or 3 fails,** it does not merge. **If only 4 fails,** it does not merge as the default, and the fired runs are listed.

## Predictions

| quantity | prediction |
|---|---|
| F | same as the near-args replay (100% / 99.8%) |
| legacy stops reproduced by new only | the 3 above, plus 0 to 3 more of the same kind (`run_shell` or interpreter walls) |
| write-tail stops reproduced by new only | 0 |
| new-only fires in `brk-fixed-strong` | 0 |
| new-only fires in `brk-fixed-weak` | 0 to 2, in runs below their cell median |
| calls read as an answered failure while the loop saw a success | a few percent of all `run_shell` / `execute_code` / `code_interpreter` calls (test runs that fail are the bulk) |

## What this cannot show

- **Anything about outcomes where the rule never fires.** Whether stopping at such a wall is better than continuing is unmeasured, as it was for the near-args rule.
- **Tools whose failures look different.** MCP tools and browser errors are not covered.
- **The live observation.** The replay reads the trace's copy, clipped to 800 characters, head and tail. The `[exit N]` marker at the head and an exception at the tail both survive the clip.
