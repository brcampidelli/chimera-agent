# Results — pilot only. The registered run must not be paid for on this corpus.

Run 2026-08-28 · 5 tasks × 3 arms × 1 seed, plus a 15-solve screen · prereg `PREREGISTRATION.md`
with `ADDENDUM-01` and `ADDENDUM-02`.

**Read the pre-registration first.** The arms, the seeds, the adoption criterion and the conditions
for calling a run void were fixed there before the first model call. This file reports what the
pilot bought, and the pilot bought a decision: **do not run the registered 900 cells on this
corpus.**

## What the pilot measured

| | |
|---|---|
| screen — `claude-opus-5` / `gpt-5.5` / `gemini-3.1-pro-preview` | **5/5 each**, 15/15 |
| `B_repeat` (opus ×3, gate keeps the first pass) | **5/5** |
| `C_single` (opus ×1) | **5/5** |
| `A_fusion` (the shipped `--fuse` route) | **3/5** — and both misses are the clock |

## Three reasons the registered run would not have meant anything

### 1. The corpus is at ceiling, so there is nothing for an aggregator to win

Twenty-five single-model solves, twenty-five passes. `local_lift` was authored to discriminate a
**weak** model; against frontier models its first five tasks are trivial. A comparison whose control
arm sits at 100% cannot show a lift or a loss in either direction — the instrument cannot exhibit
the effect, which is the `§2q` condition for a result that says nothing while looking like one.

This is the **third** time this project has run into an authored suite's ceiling. The learning-lift
series tried three times to land a control arm in a 40–60% band and got 84–92% every time, and
closed with the sentence that applies here word for word: *"we cannot build a synthetic suite that
can measure it."*

### 2. At ceiling, arm B collapses into arm C — the equal-budget comparison degenerates

Arm B is three samples with the gate keeping the first that passes. Every first sample passed, so
`samples_paid` was **1** in all five cells and arm B never bought samples two and three. Measured
cost confirms it: on the three tasks where every arm left a receipt, B spent 178,098 tokens against
C's 165,899.

So the primary comparison A-vs-B, which the design chose precisely because it holds the budget
equal, is on this corpus a comparison against a **single** sample. The equal-budget arm only exists
when the model sometimes fails — which is another way of saying the same thing as §1.

### 3. Both of arm A's misses are the timeout, not the model

`path_get` and `eval_expr` failed at **300.1s** against a `--timeout 300`. Fusion runs 3–4× slower
than the single arms (164–234s where they take 34–88s), so the wall lands on one arm and not the
others.

A full run at this timeout would have reported a large A-vs-B deficit that is a **clock artifact**,
and nothing in the pass/fail column would have said so. This is exactly the conflation the
learning-lift series had to separate after run 7a: *"the agent could not do it"* and *"the clock ran
out"* were the same number.

## What the tokens say, on the part that is readable

Only three of five tasks left a receipt in **every** arm, so only those three can be compared:

| arm | tokens, paired-complete subset (3 tasks) |
|---|---|
| `A_fusion` | 190,549 |
| `B_repeat` | 178,098 |
| `C_single` | 165,899 |

**A/C = 1.15** — fusion moved the spend by +15%, which is the activation reading `ADDENDUM-02`
registered: the fused planning turn fired. **A/B = 1.07**, and criterion 3's ceiling is 2.0, so cost
was never going to be what disqualified fusion here.

There is no accuracy signal to put beside these numbers: every arm passed all three.

## An apparatus finding worth more than the run: a successful solve can leave no receipt

On `eval_expr`, **all three arms wrote no row to `runs.jsonl` at all** — and two of them succeeded.
The workspace still holds the written `calc.py`, and its test passes on a fresh check. Two more
cells lost their receipt to the timeout kill. Four of fifteen cells therefore have a cost that is
**unknown, not zero**, and the pilot's `NO ESTIMATE` guard refused to print a full-run projection
built on them.

The mechanism is in the product, not in the bench. `AutonomousAgent._persist_receipt`
(`chimera/core/autonomous.py:1230`) wraps the whole write in `except Exception` and logs at **debug**.
A receipt that fails to serialise disappears with no trace at any level a user or a bench would see,
and it disappears **by task** rather than at random — so every consumer of those receipts, the Cost
screen included, undercounts systematically and silently.

## Two defects in this harness, caught by its own output

- The token counts printed by the run **double-count an earlier run of the same cell**. The
  workspace path repeats between runs, and the process had already loaded its code when the scoping
  fix landed. Every number in this file is recomputed from `runs.jsonl` scoped to the pilot window;
  the fix is committed and tested.
- The harness discards the solve's exit code and output, so a cell that joins no receipt leaves
  **no evidence of what happened**. That is why the `eval_expr` diagnosis had to be done from disk.

## What would have to change before the registered run is worth paying for

1. **A corpus whose single-model arm lands in a measurable band.** Not authored to be hard — chosen
   because it already is. SWE-bench Verified is the one this project has already registered for
   exactly this reason: difficulty nobody here authored, and a grader nobody here owns.
2. **A timeout that does not truncate the slowest arm**, with timeouts recorded and audited per arm
   and an asymmetric timeout invalidating the run outright — the rule the learning-lift series
   already had to write.
3. **A receipt that cannot vanish**, or a cost source that does not depend on one.

Until then the honest statement is that fusion-as-shipped and a frontier model both solve these
tasks every time, fusion costs 15% more than one frontier pass to do it, and **this corpus cannot
tell them apart** — which is a fact about the corpus and not about fusion.
