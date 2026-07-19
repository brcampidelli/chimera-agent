# Pre-registration — standard LLM benchmarks across the model-quality ladder

**Written and committed BEFORE any model call.** This commit is the pre-registration. Whatever the
numbers say, they get published — including a null or a negative.

## The question

Bruno's question: *how much does Chimera improve a weak model, a mid model, a good model, an excellent
model?* Prior work in this repo answered it once, on our own suite
(`bench/local_lift`, n=100, +6.0pp, CI [+1.3, +6.0]). That suite is ours, which is exactly the
objection a skeptic should raise. This runs the same question on **standard, external benchmarks**.

## Which benchmarks — and the ones we deliberately refuse to run

Chosen: **HumanEval** (164 tasks, execution-graded) and **GSM8K** (test split, exact-match numeric).

**Refused, on principle: MMLU, HellaSwag, ARC, WinoGrande, TruthfulQA, SafetyBench** and every other
multiple-choice knowledge benchmark. They are single-forward-pass A/B/C/D questions. An agent loop has
no lever on them: no tool helps pick a letter, and there is no verification signal to iterate against.
Running them would burn tokens to produce noise centred on zero — and any "+0.4pp" we then reported
would be noise-mining, the exact practice this project forbids. We say this publicly rather than
quietly omitting them; a project that states where it does *not* help is more trustworthy, not less.

## Contamination — why it does not invalidate this

HumanEval and GSM8K are both in every modern model's training data. That inflates **absolute** scores.
It does **not** invalidate the comparison, because this is a **paired within-model design**: the same
model, the same prompt, with and without the loop. Contamination lifts both arms equally and cancels
in the difference. We therefore claim a **delta**, never a leaderboard position.

## The design

Paired, per task, per model. Both arms get the identical problem statement.

* **baseline arm** — one completion, no tools, no loop. The model answering once.
* **chimera arm** — the full loop: plan, tools, verify-or-revert, retry.

### The integrity rule that makes or breaks this benchmark

**The grading tests never enter the solve workspace.** If the agent could run HumanEval's hidden unit
tests, it would not be solving the problem — it would be overfitting to the grader, and the result
would be worthless. Enforced structurally, not by convention:

1. the agent solves in workspace A, which contains **only** the prompt/stub;
2. the produced file is copied into a **separate** grading workspace B, which the agent never sees;
3. the hidden tests run in B.

The agent's own verification signal comes **only from information the prompt already gave it**: the
docstring examples, run as doctests. That is what a developer does with a spec, and it uses zero
information the baseline arm did not also receive.

### Grading

* HumanEval — execution. The canonical `check(candidate)` test runs in workspace B. Non-zero exit,
  crash, or timeout = FAIL. The model's self-report is never trusted.
* GSM8K — exact match on the final numeric answer (the reference format after `####`).

A timeout is scored an honest **FAIL**, not excluded. Excluding timeouts would flatter whichever arm
is slower, which is always the chimera arm.

### Statistics

`chimera.eval.paired.compare_paired` — McNemar on discordant pairs, Wilson interval on the delta.
**Significant iff the 95% CI excludes zero.** One shot per configuration. **No re-rolls**: a
disappointing run is reported as-is, because re-running until it looks good is p-hacking.

## The model ladder

| tier | model | why |
|---|---|---|
| weak | `openrouter/mistralai/mistral-small-3.2-24b-instruct` | the local-lift goldilocks model |
| mid | `openrouter/deepseek/deepseek-chat-v3.1` | competent, cheap, tool-capable |
| good / excellent | *deferred* | costs ~US$60+ per suite; runs only on Bruno's explicit budget approval |

This run covers **weak and mid only**, under a hard budget cap of **US$1.00**. The runner aborts if
projected spend crosses it. Reporting two tiers and saying the top two were not run is honest;
extrapolating a curve from two points would not be.

## Predictions (registered in advance — the point is that they can be wrong)

1. **Weak tier shows positive lift** on HumanEval, replicating `bench/local_lift` on an external suite.
2. **Lift shrinks from weak → mid** (ceiling effect: less headroom, fewer recoverable failures).
3. **GSM8K lift ≥ HumanEval lift on the weak tier** — arithmetic is where a code interpreter converts
   a reasoning failure into a mechanical one, and weak models fail arithmetic more than they fail
   syntax.
4. **Absolute rates will be far above `bench/local_lift`'s 9–15%** — HumanEval and GSM8K are easier
   *and* contaminated. If our HumanEval baseline lands near published figures for the same model, that
   is a sanity check on the harness; a wild divergence means the harness is wrong, not the model.
5. **A negative result is live.** If the loop costs ~10x the tokens and returns no significant lift on
   a standard benchmark, that is the finding, and it gets published with the same prominence as a win.

## Amendment 1 — 2026-07-19, after the smoke run, BEFORE the measured run

A 5-task smoke run on the weak tier exposed **three harness bugs**. All smoke data is **discarded**
under the discard rule below, the journal was deleted, and the run restarts from zero. Disclosed here
rather than quietly fixed, because the fixes were informed by seeing bad numbers — which is exactly
the situation where silent tuning becomes p-hacking.

1. **The agent's gate never failed.** `python -m doctest solution.py` exits 0 even when the examples
   fail. The chimera arm was running with no verification signal at all. Fixed to
   `doctest.testmod(...)` with an explicit exit code, and tested against known-correct / known-wrong
   / stub / no-examples inputs before re-running.
2. **Stale bytecode.** Without `-B`, an edit that keeps the file the same *size* within the same
   mtime second reuses the cached `.pyc`, so the gate grades the **previous** version. `return a + b`
   → `return a * b` is that exact shape — and so is an agent iterating on one function. This would
   have silently corrupted every chimera-arm run.
3. **`--max-steps` was left at the CLI default of 8**, below the floor for a single read → edit →
   verify → diagnose → re-edit → re-verify cycle. Attempts were failing mechanically, for want of
   steps rather than want of ability. Set to **15** for the measured run: enough for two full
   iterations, not unbounded. This value is fixed now and will not be touched again.

**A finding the smoke made concrete, kept as a live hypothesis rather than a fix:** a failed attempt
**reverts the workspace** ([autonomous.py:530](../../chimera/core/autonomous.py)), so the chimera arm
can be graded on a pristine stub while the baseline always produces *something*. That asymmetry is
real, it is verify-or-revert working as designed, and it may well mean the loop scores **below** the
raw model on HumanEval. We are not "fixing" it — grading the agent's discarded artifact would measure
something no user ever gets. If the loop loses because it throws away work it cannot verify, that is
the result, and it gets published.

## What would make us discard a run (declared now, not after seeing results)

* Harness bug proven by a reference implementation disagreeing on a task both arms should pass.
* Provider outage/rate-limit producing systematic errors in one arm (both arms re-run, not one).
* Anything else is reported. In particular: "the result was worse than expected" is **not** grounds.
