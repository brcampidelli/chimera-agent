# The loop breaker and failures the tool reports itself: replay results

*2026-09-24 · **US$ 0**, no solve · pre-registration committed and pushed before the replay ran (a8af86b) · `python3 bench/tool_loop_silent_failure/replay.py` reprints it.*

## Verdict: all four criteria hold, and the rule merges as the default

| criterion | outcome |
|---|---|
| F: fidelity ≥ 95% in every group | **holds**: identical to the near-args replay (100% in every study-24 family, 1,247/1,249 earlier) |
| 2: no write-tail stop with distinct args reproduced by the new rule only | **holds**: 0 of 86 |
| 3: zero new-only fires in `brk-fixed-strong` | **holds**: 0 of 37 runs |
| 4: no new-only fire in a `brk-fixed-weak` run above its cell median | **holds, vacuously**: 0 fires in 39 runs |

**The rule reproduces exactly the three stops it was written from, and nothing else.**
- Two are `run_shell` given its command as a list, answering `[exit 127] /bin/sh: 1: [bash,: not found`.
- One is `code_interpreter` answering `ZeroDivisionError` four times.

As the registration said, this is circular: it shows the rule does what it was written to do.

The evidence that it does no harm is the rest of the table:
- none of the 86 distinct-write false alarms of #577 comes back;
- it would fire in none of the 76 runs that continued under the fixed rule.

**As with the near-args rule, criterion 4 holds because nothing happened.** No cost is measured, and no gain in a continuing run either.

## How much it acts

The rule is broad in what it reads and narrow in what it stops. Of the calls to the three tools that the loop recorded as a success, it reads 34.8% as failures:

| tool | calls the loop saw as a success | read as a failure |
|---|---:|---:|
| `run_shell` | 5,612 | 1,824 (32.5%) |
| `execute_code` | 984 | 748 (76.0%) |
| `code_interpreter` | 1,423 | 218 (15.3%) |

Five silent non-zero exits, with nothing printed after the marker, were not counted, as designed.

It still stops only 3 of 138 legacy runs. A stop needs four consecutive calls to the same tool with the **exact** same answer, and failing test runs differ in their timings and names.

**The prediction was wrong.** I registered "a few percent of these tools' calls", and it is 34.8%. The per-tool denominators are post hoc: the registered script counted only the numerator. `posthoc_denominators.py` computes them from the same traces with the same helpers.

## An apparatus finding the count exposed (not registered)

**Almost every `execute_code` call in these traces failed before running anything.** 743 of the 748 read as a failure are `[exit 127] /bin/sh: python: not found`, and 594 `run_shell` calls hit the same error.

- **The cause in the harness.** It runs Chimera's own entry point from a venv without activating it, on a WSL Ubuntu that has `python3` but no `python`.
- **The cause in the tool.** `ExecuteCodeTool` runs the literal command `python "<script>"`.

So `execute_code` never worked in the #453 factorial, the M6 runs or the fork. The same was true in every arm, so the comparisons between arms stand. Absolute scores may sit lower than they would with a working interpreter, and the tool's share of the work was zero.

**It is also a product defect.** Any machine without a `python` alias gets exit 127 on every `execute_code` call: Debian and Ubuntu without `python-is-python3`, current macOS. That is fixed separately.

## Against the predictions

| prediction | outcome |
|---|---|
| F as in the near-args replay | **confirmed** |
| the 3 source stops, plus 0–3 of the same kind | **confirmed at the low end**: exactly 3 |
| 0 write-tail stops | **confirmed** |
| 0 new-only fires on strong | **confirmed** |
| 0–2 on weak, below the cell median | **confirmed**: 0 |
| a few percent of these tools' calls read as a failure | **refuted**: 34.8% (above) |

## What this cannot show

The registered list stands:
- outcomes where the rule never fires;
- MCP and browser failures, whose answers look different;
- the live observation (the replay reads the clipped trace).

Added by the outcome: how stopping at these walls compares with continuing. None of the 76 continuing runs contained one.
