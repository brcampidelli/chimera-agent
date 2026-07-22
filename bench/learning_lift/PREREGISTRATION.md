# Pre-registration — does accumulated learning actually help?

**Committed before any model call.** Design, task order, metric, predictions and the null-handling
rule are fixed here first, so no choice below can be made after seeing a number.

## The gap this closes

Chimera calls itself self-evolving. It has the machinery — learned skills gated on recurrence + a
transfer test + a governance validator, anti-pattern cards distilled from recurring failures, a
persistent memory with provenance, a diff-gate that refuses to count a step whose real working-tree
diff changed nothing.

**None of it is measured.** `chimera/eval/continuous.py` measures whether performance *holds* across
chained tasks (the anti-degradation proof) — not whether it *improves*. And the headline weak-model
lift benchmark disables learning outright in both arms:

```python
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills"]   # bench/local_lift/run_paired.py
```

So the project's flagship number measures the **scaffold within one task**, and says nothing about
accumulation across tasks. That is an honest experiment; it is just not this one. This bench is the
missing symmetric half: with learning ON and carried forward, does the agent get better as it goes?

## Why this task suite

Learning is only *measurable* where transfer is *possible*. Of the 100 tasks in `bench/local_lift`,
only one family has more than two members: **`fix_*`, 30 tasks**, all sharing one shape — *"the
package `X` has a bug: `FUNC` in `X/file.py` does Y instead of Z; fix it"*, one file each.

Running this on the full 100 would very likely produce a null **by construction** — 70 one-off tasks
with nothing to transfer between them — and a null like that is uninformative, because it cannot
distinguish "learning does not work" from "there was nothing to learn". Choosing the family with real
shared structure is what makes a null result mean something. It also makes this the *friendliest*
suite for the hypothesis, which is stated plainly here so a positive result is read with that in mind.

## Design

Two arms over the **same 30 tasks in the same fixed order** (`tasks.py` order, committed).

| | Arm | Learning |
|---|---|---|
| **A** | `cold` | `--no-evolve-skills --no-remember --no-collect`, fresh `CHIMERA_HOME` **per task** |
| **B** | `learning` | evolution + memory ON, **one `CHIMERA_HOME` carried across all 30 tasks in order** |

Everything else is identical: same model, same timeout, same scaffolding flags, same fresh workspace
per task, same grader.

### The metric — difference-in-differences

Comparing arm B's second half to its first half alone would confound learning with the tasks'
intrinsic difficulty ordering. So the estimate is a **DiD**:

```
learning effect = (B_second − B_first) − (A_second − A_first)
```

Arm A's half-to-half difference is the drift caused by task ordering and noise with no learning
present. Subtracting it leaves the part attributable to accumulation. First half = tasks 1–15,
second half = 16–30, split by the committed order.

Reported alongside: per-arm overall pass rate, per-half rates, and the paired per-task grid.

### Grading

The hardened grader from `run_paired.py`: the pristine test is restored before every verdict, and any
arm that modified its own test is recorded and its workspace preserved. Solve still receives the test
as its `--verify` gate — that is the regime being measured — but it cannot grade itself.

## Pre-registered predictions

1. **Primary:** DiD > 0 (arm B improves more across halves than arm A).
2. **Skills actually learned in arm B > 0.** This is a *validity check, not a result*: if arm B
   finishes having learned nothing, the experiment measured nothing, and it will be reported as
   **"no learning occurred to measure"** — never as evidence that learning does not help. This is a
   live possibility: two of the acceptance gates were found broken during the audit that motivated
   this bench (the strict mode could not accept any result at all; the default mode over-accepted).
3. **Cost:** arm B spends more tokens than arm A (proposing and testing skills costs calls). Any lift
   has to be read against that, so both arms' token spend is recorded.

## Power — stated before the run, not after

n=30 split into halves is **small**. With 15 tasks per half per arm, only a large effect will clear
zero. A null result here means **underpowered**, not **no effect**, and will be labelled that way.
Anyone who reads a null from this as "learning is useless" is reading it wrong, and this paragraph
exists so that reading cannot be retrofitted.

## Rules that bind this run

- **One run.** No re-rolls. The first complete result is the result.
- **No post-hoc exclusion.** Every task in the committed order counts, including ones both arms fail.
- **A null ships.** If DiD ≤ 0, it is published with the same prominence as a win — the project's
  Terminal-Bench null is the precedent.
- **Order is fixed here.** Not re-shuffled after seeing outcomes.

## What this cannot answer

One model, one seed per cell, one task family, 30 tasks. It cannot say whether learning helps on
real-world repos, on other families, or at larger n. It is a first measurement of a quantity that
currently has none.
