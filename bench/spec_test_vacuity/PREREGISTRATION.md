# Pre-registration — how many generated spec tests pass on the buggy code they were meant to catch?

**Registered 2026-09-11 against `2fcf4bc` (v0.53.0 + #421–#424), before a single model call.**

## Where the question comes from

`SpecTestVerifier` (`chimera/core/spec_test.py`) is the executable gate `solve` uses when the
person gave no `--verify` command: a model writes one pytest module from the task's atomic
requirements, the module runs on the workspace as the attempt left it, and its verdict is the
attempt's `evidence="verifier"`. The generator keeps whatever it produced as long as the text
contains `def test` (line 104). Nothing asks whether the tests could have **failed**.

ExecCritic (arXiv 2609.09133, slice 13 of the 2026-09-11 sweep) states the gate this is missing:
a generated test is evidence only if it **fails on the code before the change and passes after**.
A test that passes on both did not measure the change — and the paper finds that such tests are
the majority of what generators emit. 2609.04058 (deployed silicon) is the same lesson from the
other side: a full fixed regression passed while a defect was live, *"the blind spot lies in the
instrument"*.

In `solve` the "before" exists: the attempt loop snapshots the workspace before the worker runs
(`autonomous.py:905`, `WorkspaceGuard.snapshot`) and reverts to it on failure. The verifier never
sees it.

## What is measured

The 30 tasks of `bench/local_lift` that ship a **pre-existing workspace** — the `fix_*` family,
each a small module with a planted bug and a hidden pytest that fails on it. For each task:

1. `RequirementChecklist.extract(prompt)` — the production extractor, default model, temperature 0;
2. `SpecTestGenerator.generate(prompt, requirements, code_context=workspace_digest(base))` — the
   production generator, same model, on the **buggy** workspace, exactly what `solve` would see on
   the attempt's first verify if the worker had changed nothing;
3. the generated module runs on the buggy base with `pytest -q -rA`, and each test function is
   read as `PASSED` / `FAILED` / `ERROR`;
4. **instrument check, first:** the task's own hidden test must fail on the base. A task whose
   hidden test passes on the base has no bug to detect and is excluded with the count reported.

## Outcomes

Per task: tests generated; tests that **pass on the buggy base** (they cannot detect the bug the
task is about — vacuous with respect to this task); tests that fail on it; whether the module
imported at all. Aggregated: the share of generated test functions that pass on the buggy base,
with a Wilson interval over functions and the per-task distribution beside it, because a function
count pools tasks of different sizes.

**Signature:** the fraction of tasks where **every** generated test passes on the buggy base — the
case where the shipped verifier would have reported a green `evidence="verifier"` for a workspace
that still contains the bug, had the worker done nothing.

## Registered prediction

Between **30% and 60%** of generated test functions pass on the buggy base; at least **1 in 5**
tasks has an all-passing module. Written down so it can be wrong.

## Decision rule

The base-fail gate ships if **either** number is above zero — a test that passes on the base
carries no evidence about the change, and the receipt should not name it as if it did. The change
is the smaller one the paper implies, not a rewrite: with a base snapshot available, a test that
passes before **and** after the change is **excluded** from the verdict and counted as vacuous; if
nothing remains, the verifier **abstains** (the caller falls back to its other gates, as it already
does when nothing runnable was generated); a test that passes before and fails after is a
regression and still fails the attempt; a test that fails on both is unchanged — the true negative
this gate exists for. Without a base snapshot, behaviour is byte-identical to today.

If both numbers are zero, the gate ships anyway as a guard on a defect the paper reports elsewhere,
and the null is published with the count.

## Cost

30 tasks × 2 calls (extract + generate) ≈ 60 calls, ≈ US$ 0.10. Pytest runs are local.

## What this cannot show

Only `fix_*` tasks have a base; a "create X" task starts from an empty workspace, where every test
fails on the base by construction and vacuity is impossible. One generator model, one temperature.
The gold half of ExecCritic's rule (fail-before ∧ **pass-after**) is not measured — the corpus
carries hidden tests, not reference solutions — so nothing here says how many generated tests are
*wrong* rather than *vacuous*.


## Addendum — the eight tasks the generator returned nothing on (registered 2026-09-11, after RESULTS.md, before any call)

RESULTS.md recorded eight tasks with requirements and no module (`fix_collect_items`,
`fix_percentile`, `fix_rotate_list`, `fix_merge_settings`, `fix_insert_pos`, `fix_count_words`,
`fix_first_value`, `fix_title_case`), and a re-probe of one that produced a nine-test module at the
same settings. The shipped generator made one call with **no `max_tokens`** (the provider's default
applied) and read nothing off the reply but its text. `probe_empty.py` runs the same eight tasks
through two arms, same model, same extractor, requirements re-extracted once per task and shared:

- `shipped` — one call, no `max_tokens`, as `generate` was; the reply's `finish_reason`,
  completion tokens and length recorded, which the shipped path never did.
- `retry` — the new `SpecTestGenerator`: an explicit 16,000-token budget, one retry on a reply with
  no `def test` in it, the budget doubled after a `length` finish, `finish_reason` and attempts
  recorded.

Each arm is read as *module or nothing* per task, plus the `finish_reason` of every reply.

**Prediction.** The `shipped` arm returns nothing on at least 4 of the 8 again (the failure is
route- or run-dependent, not deterministic, so not all eight); the recorded `finish_reason` on
the empties is `length` on the majority (the reasoning spent the default budget). The `retry` arm
returns a module on at least 6 of 8.

**Decision.** The retry and the explicit budget ship either way — a silent abstain that now says
why is the smaller half of the change and costs nothing. What the numbers decide is the sentence:
if the empties are mostly `length`, the changelog says the default budget was the cause; if they
are mostly `stop`, it says the model declined and the retry is what recovered it; if the `retry`
arm recovers fewer than 4 of 8, the changelog says the retry did not fix it and names the number.

**Cost.** 8 tasks × (1 extract + 1 shipped + ≤ 2 retry) ≤ 32 calls on the default tier,
≈ US$ 0.10; the reasoning model's time is the real cost (the first run averaged 710 s per task).

**What this cannot show.** Eight tasks, one model, one day's routes; the `shipped` arm's empties
are a rerun of a run-dependent failure, so its count is one draw, not a rate.
