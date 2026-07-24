# Resume note — retry-lift, paused at a decision point (2026-07-24)

Bruno chose "stop here, decide later" after run 1c completed as a **pre-registered measurement
failure**. Nothing is written to a RESULTS.md yet; that decision is still open. This note is the
full state so it can be picked up cold.

## The question (unchanged)
Does conditioning a retry on its own failed attempt help? Two interventions, separate arms:
- **I1 `--diff-feedback`** — feed the failed attempt's reverted diff back, framed as a path not to retake.
- **I2 `--stagnation-fuzzy`** — approximate signature matching so the stall pivot fires on same-cause
  failures with different assertion text.
Both implemented behind flags, off by default, committed. Design fixed in `PREREGISTRATION.md` (+ Amend. 1).

## Runs so far
| run | status | why |
|---|---|---|
| 1a | discarded | I2 firing counter was unreachable (counted an event `--replan` preempts). Fixed. |
| 1b | orphaned | session restart severed its stdout pipe; process would crash on next print without writing retry.json. Salvaged: `results/run1b_stdout.log` (seeds 1–2 complete, all arms, n=80/arm). |
| **1c** | **completed, but FAILED 2/7 validity gates → measurement failure by rule** | canonical `results/retry.json` + `results/run1c_stdout.log`. |

## Run 1c numbers (which the gates say NOT to treat as evidence)
```
I1 vs control (n=120): control 65.0%  i1 60.8%  Δ -4.2%  CI[-11.8%,+4.5%]  ns
I2 vs control (n=120): control 65.0%  i2 65.8%  Δ +0.8%  CI[-6.9%,+8.3%]   ns
retry lens: I1 -7.0% ns, I2 +3.5% ns
```

## The two gate failures (both minor, both bias toward the null anyway)
1. **timeout symmetry** — i1 had 2 timeouts / 120, other arms 0. Gate threshold `worst-best<=1` fails
   at 2. This is API slowness, not the whole-arm collapse gate 2 was built for (run 7a). The 2
   timeouts count as i1 failures, i.e. they push i1 *down* — they cannot have manufactured a positive.
2. **grading integrity** — `hfix_merge_ranges` had its test modified. Inspected: the change was
   **additive** (agent prepended extra assert cases; the original hidden asserts remained), the grader
   restored pristine and graded honestly. So the pass/fail data is not corrupted — but the gate is
   "any modification fails," so it fails. (Same task tampered in learning-lift run 4 — this task
   specifically induces the model to write into the test file.)

## The honest cross-run read (does not depend on 1c being valid)
I1 gave **+6% in 1b and −4% in 1c** measuring the identical thing. A real effect does not flip sign on
replication. The signal oscillates around zero → the pre-registered "I1 null" reading. I2 is +0.8% with
its wire now proven live (19 stall responses vs 7 control in 1c; the 1b 4-vs-4 tie that looked "inert"
was resolved — approximate matching *does* fire more, and still changes nothing). **Verdict: neither
intervention is proven to help; both trend strongly null. But because 1c tripped its own honesty gates,
this cannot be upgraded to "proven null" — it is "unproven, trending null."**

## Two gates are MIS-CALIBRATED (a methodological finding, direction-independent)
Stated so a future amendment can fix them BEFORE any re-run — never after seeing a result:
1. **grading gate** should distinguish *weakening* a test (corrupts the measurement) from *additive,
   caught-and-reverted* tampering (the grader working as designed). Catching tampering is success, not
   a measurement failure.
2. **timeout gate** threshold of 1 is too tight for occasional API slowness. A small fraction of
   timeouts in one arm (e.g. ≤ ~3% and not concentrated) is not the run-7a collapse. Pick a
   fraction-based threshold, justified by the run-7a failure mode (a whole arm degrading), not a raw count.

## The open decision (what "decide later" is choosing between)
- **(A) Amend the two gates + one clean run (~$1.5).** Produces a gate-passing verdict → can finally
  say "proven null" instead of "trending null." The amendment must be committed before the run and its
  justification must not reference 1c's result direction.
- **(B) Accept trending-null, write RESULTS.md now.** No more spend. Documents everything incl. the gate
  failures; verdict stays "unproven, trending null."
- Do NOT loosen the gates and re-interpret 1c — that is the cardinal sin. Any gate change applies only
  to a FUTURE run.

## Budget
Key `chimera-learning-lift-5usd`, limit raised to $10, **~$5.10 left**. A clean 360-solve run ≈ $1.5.

## Launch recipe (WSL)
`scratchpad/retry.sh` sets: `BENCH_TIMEOUT=480 BENCH_SEEDS=3 BENCH_OUT=results`,
`UV_PROJECT_ENVIRONMENT=/tmp/ciwsl-venv`, run via `MSYS_NO_PATHCONV=1 wsl -e bash <script>` and
**redirect stdout to a real file** (not the task pipe) so a restart can't orphan it. Model =
mistral-small-3.2-24b. Parser: `python bench/retry_lift/parse_partial.py <log>`.
