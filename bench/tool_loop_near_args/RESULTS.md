# The loop breaker's near-identical miss: replay results

*2026-09-24 · **US$ 0**, no solve · pre-registration committed and pushed before the replay ran (53ee906) · `python3 bench/tool_loop_near_args/replay.py` reprints everything below from the traces in `~/hb-homes`.*

## Verdict: all four criteria hold, and the wider rule merges as the default

| criterion | outcome |
|---|---|
| F: fidelity ≥ 95% in every group | **holds**: 100% in every study-24 family, and 1,247/1,249 = 99.8% in the earlier traces |
| 2: new rule reproduces zero write-tail stops with distinct args | **holds**: 0 of 86 |
| 3: zero new-only fires in `brk-fixed-strong` | **holds**: 0 of 37 runs |
| 4: no new-only fire in a `brk-fixed-weak` run above its cell median | **holds, vacuously**: 0 fires in 39 runs |

**Criterion 4 passed because nothing happened.** In the 76 runs that continued under the fixed rule, the wider rule would have fired nowhere. The replay therefore shows no cost, and it also shows no gain in a continuing run. The change rests on how narrow it is, not on a measured benefit:
- it reproduces 5 of 138 legacy stops;
- all 5 are the same pattern, the model re-listing the workspace root;
- it reproduces none of the #577 false alarm.

## 1. Fidelity

Each run's own rule, replayed on its clipped trace, has to put its first fire in the step where the live run recorded its trip.

| group | rule | agreement |
|---|---|---|
| `brk-fixed-strong` · `brk-fixed-weak` | fixed | 37/37 · 39/39 |
| `brk-legacy-strong` · `brk-legacy-weak` | legacy | 39/39 · 38/38 |
| `m6-stop-*` · `m6-esc-*` | legacy | 36/36 · 37/37 |
| `m6f-strong` · `m6f-weak` | legacy | 70/70 · 479/479 |
| earlier traces (the #453 factorial and the study-24 System One runs) | legacy | 1,247/1,249 |

**The two disagreements** are both System One weak runs on task 016. In each, the replay fires **earlier** than the live run did. The tails are `apply_patch` calls whose arguments were clipped at 400 characters in the trace, so patches that differed only in their middle look identical to the replay. This is the clipping artefact the registration named. `results/posthoc_neither.txt` lists both.

## 2. Legacy stops, classified

For each legacy stop: would the fixed rule stop at the same step, would only the wider rule, or would neither?

| | fixed reproduces | new only | neither |
|---|---:|---:|---:|
| writes, distinct args | 7 | **0** | **86** |
| writes, identical args | 5 | 0 | 0 |
| reads | 21 | **5** | 5 |
| mixed | 5 | 0 | 4 |
| **total (138)** | 38 | 5 | 95 |

- **The fixed rule reproduces 38 stops.** It still stops the 7 with distinct-args writes, through its wall rule (the same failure, whatever the args) or the ping-pong rule, which the fix did not change.
- **The 86 "writes, distinct args · neither" stops are the #577 false alarm**, which stays closed:
  - 11 in `brk-legacy-strong`;
  - 1 in `brk-legacy-weak`;
  - 6 in `m6-*`;
  - 20 in `m6f-strong`;
  - 48 in the earlier traces.

**The 5 new-only stops** are all `list_dir` of the workspace root, answered `in/\nout/`:

| run | the four calls |
|---|---|
| `m6f-weak` 042 r58 | depth 2, 3, 2, 1 |
| `m6f-weak` 043 r35 | `max_results` 200, absent, absent, 200 |
| `m6f-weak` 082 r44 | depth 3, 4, 2, 2 |
| System One weak 039 r2 | path `""` three times, then `"."` |
| System One weak 043 r0 | depth 3, 3, 4, 4 |

## 3. Against the predictions

| prediction | outcome |
|---|---|
| F ≥ 98% | **confirmed**: 100% and 99.8% |
| new reproduces the fork's 3 root listings plus a handful more, all reads | **confirmed**: 3 + 2, all root listings |
| 0 write-tail stops reproduced | **confirmed** |
| 0 new-only fires on strong | **confirmed** |
| 0–4 new-only fires on weak, below the cell median | **confirmed at the edge**: 0 |

## 4. A second miss, found post hoc (not registered, not fixed here)

`posthoc_neither.py` lists the read and mixed legacy stops that no rule reproduces. Most are work:
- the strong model globbing several patterns that match nothing;
- `code_interpreter` defining functions that print nothing;
- edit, test, edit, test.

**Three are walls that neither rule sees.** In each, the same failure repeats with different arguments, and the answer does not start with `error:`. The trace, and the loop, therefore record the call as a success:
- `run_shell` given the command as a **list** (twice: `brk-legacy-weak` 041 r8 and `m6f-weak` 041 r66). Every call answers `[exit 127] /bin/sh: 1: [bash,: not found`, whatever the command inside the list.
- `code_interpreter` answering `ZeroDivisionError: float division by zero` four times (System One weak 089 r2).

The fixed rule catches "the same failure, whatever the args" only when the failure is marked as one. A non-zero exit, or an exception inside the interpreter, is not marked that way. This would need its own registration: what counts as a failure for the breaker is a separate question from what the model is shown.

## 5. What this cannot show

- **Any effect on outcomes.** None of the 76 continuing runs contained the pattern, so how stopping at a root re-listing compares with continuing is unmeasured. The fork measured escalating at these three trips against stopping there, not stopping against continuing.
- **Surfaces other than the harness-bench solve,** and tools other than those these runs used.
- **Live arguments and observations.** The replay sees the trace's clipped ones: arguments at 400 characters, observations at 800.
