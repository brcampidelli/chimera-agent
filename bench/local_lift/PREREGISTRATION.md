# Pre-registration — weak-model-lift, n=100

**Written and committed BEFORE the new tasks were authored and BEFORE any model call of the n=100
run.** Its whole purpose is to bind our hands: everything below is decided while we cannot yet know
the outcome. Commit hash of this file's introduction is the timestamp that matters.

## The question

> Does driving a *weak* model through Chimera's full loop (plan → attempt → verify → revert → retry)
> beat the same model answering once, on the same task, from the same starting state?

This is the project's central claim. It is currently **unproven**: at n=15 the loop scored
60.0% → 73.3% (+13.3pp), 95% CI **[−4.2%, +13.3%] — which includes zero**.

## Why the previous run was inconclusive, stated before we re-run

Not because n was small in itself. **McNemar only sees discordant pairs** — tasks where the two arms
*disagree*. At n=15 there were exactly **2** (both loop-wins, zero loop-losses). A task both arms
pass, or both arms fail, contributes **nothing** to the test no matter how many you add.

Of the 9 tasks added in the n=6→n=15 expansion, **7 were one-shot passes** for this model. They grew
n and shrank nothing else. Adding 85 more of that kind would buy a bigger number and the same verdict.

**This is registered here so we cannot "discover" it after the fact and retrofit the story.**

## Design (fixed now)

| | |
|---|---|
| **N** | **100** (the existing 15 + 85 new) |
| **Model** | `openrouter/mistralai/mistral-small-3.2-24b-instruct` (unchanged — a *goldilocks* model: weak enough to fail some tasks one-shot, capable enough that a loop can recover some) |
| **Arms** | baseline = `--no-plan --no-manager --max-attempts 1` · treatment = `--repo-map --progress-ledger --checklist --replan --max-attempts 3`. Both hygiene-flagged (`--no-remember --no-collect --no-evolve-skills`). Unchanged from the n=15 run. |
| **Pairing** | each task's workspace is restored to an identical fresh state before EACH arm (`run_paired.py`) |
| **Grading** | the task's own strict pytest, re-run independently after the solve. Never the agent's self-report. |
| **Primary endpoint** | paired McNemar/Wilson on the pass/fail contrast; **significant only if the 95% CI excludes 0** |
| **Timeout** | 240s per arm. A timeout is scored an honest FAIL (not excluded). |

## Keeping the existing 15 — deliberately

The 15 pre-registered tasks stay in, **including the 7 the model one-shots**. Dropping tasks that
produced no signal *after seeing that they produced no signal* is post-hoc exclusion — it would
inflate the effect and invalidate the whole thing. They cost us power. They stay.

## How the 85 new tasks are chosen — the anti-cherry-pick rule

Authored to an **a-priori difficulty spec**, never to an outcome:

- **Allowed** selection criterion: *intrinsic task complexity* — requires ≥2 non-obvious steps, or has
  edge cases a naive implementation misses (empty input, boundaries, malformed input, ordering).
  Each task ships a strict pytest that a correct reference solution passes and a naive one fails.
- **Forbidden**: authoring, tuning, or keeping a task based on how either arm *performed* on it. No
  piloting tasks and keeping the ones the loop wins. No rewriting a task after seeing its result.
- Tasks are **committed before the run**. The commit is the pre-registration.
- Domain mix is fixed up front (parsing, data structures, algorithms, string/format handling, small
  bug-fixes in given code), so the suite isn't quietly steered toward what the loop is good at.

**The population this measures**, stated plainly: *small, self-contained Python tasks with strict
tests, at mixed difficulty for a 24B model*. It is **not** SWE-bench, not a real-world repo, and the
result does not generalise to either. The suite label says so.

## Analysis plan (fixed now)

- **One run. No re-rolls.** If the result is not significant, that is the result. Running it again
  until it crosses is p-hacking — the exact dishonesty this bench exists to refuse.
- No post-hoc task exclusion. No swapping the model. No changing the arms.
- The primary number is the paired CI. The unpaired Newcombe number is reported alongside, as before.
- Every task's raw pass/fail lands in `results/paired.json` and the per-task grid is published.

## What we publish — decided before we know it

**All of it, whatever it says.** Specifically, we commit in advance to publishing:

- a **null result** (CI still includes 0) — the honest read then being *"we could not show it at
  n=100 on this suite"*, which is a real finding about the size of the effect;
- a **negative result** (the loop is worse) — which would falsify the project's central claim, and
  saying so out loud is worth more than the claim was;
- a significant positive result, with its CI and its population caveat intact.

If we would only publish outcome (3), the number would be worthless. That is the point of writing
this down first.

## Known limitations (acknowledged now, not as an excuse later)

- One model. A lift here says nothing about other models or sizes.
- One seed per task per arm — run-to-run variance is not measured; a task that flips on luck is noise
  we cannot see. (Multiple seeds per task would cost N× the budget; not affordable here. Registered
  as a limitation, not discovered as one.)
- The suite is authored by the same project it evaluates. Mitigated by the a-priori spec + reference
  solutions + pre-registration, not eliminated.
