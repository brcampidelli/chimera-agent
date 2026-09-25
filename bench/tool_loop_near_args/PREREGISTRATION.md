# The loop breaker's near-identical miss: a replay registration

*2026-09-24 · US$ 0, no solve, only recorded traces · committed and pushed before the replay runs.*

## The miss

The fix in #577 makes a spin need **the same call and the same answer**. `bench/tool_loop_fix` recorded one known miss: a call that changes only its *shape* still asks the same question.

In `bench/tool_loop_fork`, the weak executor listed the workspace root four times and got `in/\nout/` each time. Only the shape changed:
- `depth` 2, 3, 2, 1 (042 r58);
- `max_results` 200, absent, absent, 200 (043 r35);
- `depth` 3, 4, 2, 2 (082 r44).

The legacy rule stopped these runs. The fixed rule does not, so `max_steps` is what bounds them.

## The change (this branch, `chimera/core/tool_loop.py`)

`_target_sig(name, args)` is the call with its shape removed:
- numbers, booleans and nulls are dropped;
- strings that are only a number are dropped, except under a path-like key;
- path-like keys (`path`, `file`, `dir`, `cwd`, … by name) are normalised, so `""`, `"."` and `"./"` are one place;
- every other value is kept whole: strings, lists and objects.

`_no_progress` also counts a call as a repeat when it has the same tool, the same target and the **exact** same observation. The digit-flattened gist is not used here, so pages of a log that differ only in timestamps stay new pages.

Unchanged:
- `_identical_repeat`;
- `_ping_pong`;
- the wall/loop wording.

## What the replay can and cannot show

The new condition is a strict subset of the legacy one. Legacy stopped on "same tool, same output, any args" (and on 5 identical args whatever the output). So on traces the legacy rule ran, the new rule can fire only where legacy already stopped the run. There is nothing after that point to observe.

| population | rule it ran | n rows | what it can show |
|---|---|---|---|
| `brk-legacy-*`, `m6-*`, `m6f-*`, and every other trace from before 2026-09-24 (the #453 factorial) | legacy | all traced | **classification** of every legacy stop: fixed reproduces it, only the new rule does, or neither |
| `brk-fixed-*` | fixed | 76 | **cost**: runs that continued past a point where the new rule would have stopped them, and how they scored |
| traces from 2026-09-24 onward outside these families | unknown | counted, not analysed | nothing |

**The cost sample is small (76 runs, 4 tasks, 2 executors), and that is all the data there is.** A new-only fire in a run that went on to score well is the one outcome that shows the rule costing something.

**The replay feeds each detector the trace, not the live call:**
- arguments are clipped at 400 characters and observations at 800, head and tail;
- the trace's `ok` comes from `tool_record`, not from the loop's own `ran`.

That is what the fidelity gate is for.

## Gates, before any result is read

**F — fidelity.**
- Replaying the legacy detector on legacy rows must put its first fire in the step where the run recorded its trip, and must not fire in rows that recorded none:
  - a recorded trip is `stopped_reason == "tool_loop"`;
  - in the escalating families (`m6-esc-*`, `m6f-*`), it is the last step before the model changes.
- The same holds for the fixed detector on `brk-fixed-*`.
- **Agreement must be ≥ 95% of rows in each group.** Every disagreement is listed.
- Below 95%, the replay does not measure the rules, and nothing below is read.

## Decision rule

The new rule merges as the default only if all four hold:

1. **F passes.**
2. **The #577 false alarm stays closed.** Among legacy stops, the new rule reproduces **zero** where the four tail calls are writes (`edit_file`, `write_file`, `apply_patch`, `multi_edit`, `create_file`) and their arguments differ.
3. **Strong is untouched.** Zero new-only fires in `brk-fixed-strong`.
4. **No measured cost on weak.** No new-only fire in `brk-fixed-weak` falls in a run whose final oracle score is above the median of its (task, weak, fixed) cell.

**If 2 or 3 fails,** the change does not merge.

**If only 4 fails,** it does not merge as the default. The miss stays documented, with the fired runs listed.

## Predictions

| quantity | prediction |
|---|---|
| F agreement, legacy and fixed | ≥ 98% |
| legacy stops the new rule reproduces that fixed does not | the fork's 3 root listings plus a handful more, all reads |
| legacy write-tail stops the new rule reproduces | 0 |
| new-only fires in `brk-fixed-strong` | 0 |
| new-only fires in `brk-fixed-weak` | 0–4, in runs scoring below their cell median |

## Reported either way

For every new-only fire in a continuing run:
- the steps and prompt tokens after the fire, which is what stopping would have saved;
- its tools and target.

The full classification table of legacy stops is reported by family.
