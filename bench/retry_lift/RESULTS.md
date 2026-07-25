# Does conditioning a retry on its own failed attempt help? — closed, unproven

Design, arms, metrics, validity gates and pre-committed readings were fixed in
[`PREREGISTRATION.md`](PREREGISTRATION.md) **before any model call and before any of the code
existed**. This closes the question at the honest label the evidence supports.

**Bottom line.** Neither intervention is proven to help, and both **trend strongly null**. The
decisive evidence is not any single run — it is a **replication failure**: intervention I1 measured
**+6% in one run and −4% in another on the identical comparison**. A real effect does not flip sign
on replication. The clean-run verdict was never obtained because the one complete run **failed two of
its own seven validity gates**, and by the pre-registration's rule a run that trips a gate reports a
measurement failure, not a result.

## The question, and why it was worth asking

When an attempt fails, the agent captures the diff that attempt actually wrote (pre-revert) — and
throws it away. So a retry is told **that** it failed and **what to focus on**, but never shown the
code it wrote that did not work; the workspace was reverted, so nothing on disk records the wrong path
either. Nothing prevents it from re-deriving the same patch.

The population was **measured before anything was built** ([`probe_attempts.py`](../learning_lift/probe_attempts.py),
n=40): **52.5%** of solves reach a second attempt, **100%** of failures exhaust the attempt budget
(so the loop is fully engaged and still losing), and recovery sits at **38.1%** — inside the 40–60%
informative band that three authored suites had failed to hit. A large, live population with real
headroom. The idea deserved the run.

## The two interventions, deliberately separated

Both act on the same headroom, so running one alone would leave any positive unassignable:

- **I1 `--diff-feedback`** — feed the failed attempt's own reverted diff back, framed as a path not to
  retake.
- **I2 `--stagnation-fuzzy`** — match repeated-failure signatures approximately, so the existing
  anti-stagnation pivot fires on same-cause failures whose assertion text differs.

## What the runs showed

| run | state | I1 vs control | I2 vs control |
|---|---|---|---|
| 1a | **discarded** — the I2 firing counter was unreachable (it counted an event `--replan` preempts) | — | — |
| 1b | **orphaned** — a session restart severed its stdout; 255 solves salvaged | **+6.2%** ns (n=80) | +3.8% ns (n=80) |
| 1c | **completed, failed 2 of 7 gates → measurement failure by rule** | **−4.2%** ns (n=120) | +0.8% ns (n=120) |

**The sign flip is the finding.** I1 came out +6.2% and then −4.2% on the same suite, same model, same
arms — a swing of ten points across replications of one comparison. That argument does not depend on
run 1c's gates, because it is about the *pattern across runs*, not the validity of one. It is what a
zero effect looks like when measured twice.

**I2 is measurably live and changes nothing.** Its wire was proven in run 1c (19 stall responses vs 7
in the control, resolving run 1b's ambiguous 4–4 tie): approximate matching genuinely fires more often
than exact matching, and the outcome moves +0.8%. Firing the pivot three times as often does not
recover the solve.

## Why run 1c does not count — and the two gates that were wrong

Run 1c tripped **timeout symmetry** (2 timeouts in one arm out of 120) and **grading integrity** (one
task modified its own test). Both defects were minor and both pushed *toward* the null, so neither
manufactured a result. But the rule is the rule, and it is stated before the numbers so it cannot be
argued away afterwards.

Auditing them showed **both gates were mis-calibrated**, in a direction independent of the result:

1. **Timeout symmetry used a raw count of 1.** The failure it exists to catch is run 7a's — a *whole
   arm* degrading until latency collapse is indistinguishable from capability collapse. Two slow API
   calls in 120 is not that. Now **fraction-based (>3% of an arm's solves)**.
2. **Grading integrity failed on any modification.** But the grader restores the pristine test before
   every verdict, so tampering **cannot** corrupt the pass/fail data — catching it is the system
   working. Inspecting the preserved evidence showed run 1c's tampering was **additive** (extra
   asserts prepended, the originals intact). The gate now fails only when pristine assertions were
   **removed** — the agent editing away its judge, which is worth discarding a run over. Tampering is
   still always reported.

**Both fixes are committed, and neither was applied to run 1c.** A gate loosened after seeing a result
is not a gate; these apply only to a future run, and run 1c stays a measurement failure.

## The honest verdict

Under the pre-registered readings this is **"I1 null and I2 null"**: the headroom those
budget-exhausting failures represent is **not** retry-conditioning headroom. Forcing a retry, or
pivoting more often, does not make a model solve what it could not solve — the residue is
capability-limited.

The label is **unproven, trending null** rather than **proven null**, because no gate-passing run
exists. A clean run (~US$1.5, ~6 h) would upgrade the label; it is not planned, because the
replication failure already answers the question and the same budget buys a more informative
experiment on real repositories.

## What survives, and what it cost

Both interventions ship as flags, **off by default**, with tests — kept for reproducibility and
because each fixes something real even though neither lifted the number:

- `--diff-feedback` closes a genuine gap (the agent's own failed diff was captured and discarded).
- `--stagnation-fuzzy` fixes a real detection blind spot (byte-identical signature matching misses
  same-cause failures with different assertion text).

Cost of the whole line of work: ~US$5.3 and three runs, one of which was discarded and one orphaned.
The durable gains were the instrumentation the failures forced: **per-solve timeouts are now recorded
and audited** (a swallowed `TimeoutExpired` had made latency collapse look like capability collapse),
and the two validity gates are calibrated to the failure they were built for.

## Methodological notes

- **Every discard is documented with its evidence** ([`PREREGISTRATION.md`](PREREGISTRATION.md)) —
  run 1a's unreachable counter, run 1c's failed gates. An undocumented discard is indistinguishable
  from a re-roll.
- **Run 1b's partial was salvaged, not silently dropped**, and is kept at
  [`results/run1b_stdout.log`](results/run1b_stdout.log) — it is the run that supplies the +6.2% this
  conclusion rests on refuting.
- **The gates were fixed only after the run they failed was already closed as invalid**, and the fixes
  are argued from the failure mode they target, never from the direction of the result.
