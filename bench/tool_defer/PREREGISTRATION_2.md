# Deferred tools, second measurement: pre-registration

Written on 2026-11-12, **before any model call was made for it**. Nothing in this directory has been
run against this document. `PREREGISTRATION.md` and `RESULT.md` (2026-09-02) are the first
measurement and are not edited by this one; this file is new and the result goes to a new
`RESULT_2.md`. Thresholds, the exclusion rules and the decision rule below are not loosened after a
run. If a result kills the default, that is the result.

## Why a second measurement

`chimera.tools.defer` puts fourteen built-in tool schemas behind `tool_list` / `tool_describe` /
`tool_call` on the Code turn and on the CLI (`docs/tool-schema-by-surface.md`). The first bench asked
whether a model that must look a tool up still completes the task, and could not answer: McNemar
p = 0.125, four discordant pairs all in one direction, and its own `RESULT.md` recommended leaving the
switch off. `defer_tools` has been **on by default** since #1210. So the question is no longer "may
it be switched on" but "does the default we ship cost completions", and the burden is reversed: the
default stays only if the measurement shows it does not.

What made the first bench unable to answer, from its `RESULT.md`, and what this one changes:

| first bench | this one |
|---|---|
| 4 of 10 tasks failed in **both** arms, so it measured task difficulty | a task is admitted only if arm A completes it (probe below) |
| `run_shell` solved several tasks without the deferred tool, 7 B runs never touched the proxy | a task is admitted only if it is **not** solvable without the tool it needs (probe below) |
| 10 tasks | at least 30 distinct tasks |
| no power statement | minimum detectable difference stated before the run |
| tokens and completion read together | completion is primary; tokens are read only after non-inferiority |

## The question, and the one it is not

> With deferral on (the shipped default), does a turn that needs a deferred tool complete as often
> as with every tool declared, within a margin of 5 percentage points?

It is a **non-inferiority** question on completion. It is not a question about speed, and it does not
cover MCP deferral (`mcp_defer`, off by default and still unmeasured), the cron and the bot (they do
not apply deferral), the five largest tool descriptions, or `compact_schemas`. Those wait for this
result.

## Arms

| arm | `CHIMERA_DEFER_TOOLS` |
|---|---|
| A (control for the question) | `false`: every built-in tool declared |
| B (the shipped default) | unset, which is `true` |

Same model, same tasks, same fixtures, same `max_steps`, same timeout, same harness
(`bench/tool_defer/run_paired.py` extended, not replaced). The model, the harness commit SHA, the
provider route and the full environment of each run are written into the result file, because the
first run did not record the model and that cannot be recovered. **One model.** Harness effects
change sign across models, so the verdict is labelled with the model it was measured on, and nothing
here licenses a statement about another one.

The two arms of a pair run interleaved in time, in an order alternated by repeat, so a provider-side
cache or a slow hour does not land on one arm.

## Units

- **Task**: a distinct (prompt, fixture, verifier) triple. The analysis unit, and the only n that is
  reported as n.
- **Repeat**: one of 3 per task per arm. A repeat is not an independent observation of the task: the
  3 repeats of one task are correlated, so rows are never counted as tasks. "Seeds" are repeat
  indices; a provider seed is passed where the route accepts one, and no run is assumed to be
  reproducible (a hosted route does not reproduce at temperature 0).
- **Pair**: (task, repeat), run in A and in B.

At least **30 tasks** × 3 repeats × 2 arms = 180 runs, plus the control and the probes below.

## Task admission

The task list is written and committed **before** the main run and is not edited after it. A
candidate is admitted by what happens in the probes, never by watching B:

1. **Deterministic verifier.** A shell command that exits 0 or 1. No model judges anything.
2. **Not solvable without the tool** (the shell probe). The candidate is run 3 times in arm A with the
   tool it `needs` denied (`CHIMERA_TOOL_DENYLIST`). It is admitted only if it completes in **0 of 3**.
   A task `run_shell` can finish is not a test of selection: success there routes around the thing
   under test, which is the failure the first bench recorded.
3. **Solvable with the tool** (the control probe). The same candidate in arm A with everything
   declared must complete in **at least 2 of 3**.
4. **The probe repeats are not main-run repeats.** Probe data is not in any analysis. Selecting on
   A's probe makes ceilings likely, and that is accepted on purpose: a ceiling reads as a bound ("no
   selection failure at this n"), which is the honest form of a null here.
5. **Spread.** At least 10 distinct deferred tools are the `needs` of some task and no tool is the
   `needs` of more than 3, so the result is not a result about two tools.
6. **Network.** At most a third need the network, served from a local fixture where the tool allows it.
   A run that fails for a network reason is excluded from **both** arms of its pair and the count is
   printed.

If fewer than 30 candidates pass the probes, the run **does not start** with fewer: the corpus is
extended first. Authoring tasks is the expensive part, and it is why the first bench stopped at 10.

## Metrics

**Primary, completion.** The verifier exits 0. Per task, the fraction of its 3 repeats that complete,
in each arm. Δ = mean over tasks of (B − A).

**Secondary, read only after the primary has a verdict:**

- prompt tokens per completed run, summed from the provider's own usage, per arm, and the B/A ratio.
  Per completed run and not per run: an arm that fails cheaply is not better than one that succeeds
  expensively;
- steps;
- the share of B runs that touched `tool_list`, `tool_describe` or `tool_call`.

**The signature of the failure mode, measured beside the aggregate.** The mechanism that would lose a
completion is *selection of a deferred tool*: the model does not look the tool up, or looks up the
wrong one. A B run is a **selection failure** when it did not complete **and** the tool its task
needs was never successfully called, **and** in the paired A run that tool was called. It is counted
per arm (A is zero by construction, since nothing is deferred there) and printed next to Δ, together
with its converse (A failed, B completed). An aggregate that falls without selection failures is
about something else, and the report says so instead of attributing the fall to deferral.

**Invariant, tested before the analysis.** A B run that completes without touching the proxy means the
admission probe was wrong for that task. The task is dropped from the analysis, counted, and named.

## Statistics and power

- **Interval** (`bench/PROTOCOL.md` §11). One difference per task — the per-task completion
  fraction in B minus A — and the one-sample t interval on their mean, `mean_t_interval` in
  `chimera/eval/proportions.py`. Reported: Δ and its 95% two-sided interval. A percentile bootstrap
  over tasks may be printed beside it as a cross-check, labelled as one; at n = 30 it does not decide
  (§11 measured such a bootstrap covering 88% at a nominal 95% under N = 100). This line replaced a
  bootstrap written into the first draft of this file, before any run, when the protocol check caught it.
- McNemar on (task, repeat) pairs is reported for continuity with the first bench. It is **not**
  decisive: repeats within a task are correlated, so its p-value is too small.
- **Power, stated now.** With 30 tasks the half-width of the interval is about `2.05 · sd / √30` (t with 29 degrees of freedom),
  where `sd` is the standard deviation of the per-task difference (a multiple of 1/3 with 3 repeats).
  Demonstrating non-inferiority at a 5 pp margin when the true difference is 0 needs `sd` of about
  0.17 or less, which means almost every task completes the same number of times in both arms. If
  the arms differ on a handful of tasks, the interval will straddle −5 pp and the outcome is
  *unresolved*, not *no difference*. That is declared in advance because it is the likeliest outcome,
  and the likeliest outcome must not be one that reads as a pass.
- **No optional stopping.** n is fixed. Repeats are not added after reading a result. If the owner
  wants more power, that is a new pre-registration with a larger corpus.

## Control arm: it must reproduce the published number

The first bench published, on its 10 tasks (`tasks.py`, unchanged): completion 18/30 in A and 15/30 in
B, **prompt tokens per completed task −26%** for B (109,495 → 81,325), McNemar p = 0.125 (4 discordant
pairs for A, 0 for B). Those tasks are run again, 3 repeats, both arms, **in the same session and with
the same harness** as the main run.

A control that does not match leaves the main result unread, because two numbers that are both wrong
the same way read as an agreement (a paired design protects against a different apparatus per arm, not
against one apparatus that is wrong for both). It **reproduces** when all of these hold:

1. tokens per completed task, B versus A, is between **−36% and −16%** (published −26% ± 10 pp);
2. arm A completes between **40% and 80%** of the 30 control runs (published 60%);
3. discordant pairs do not favour B (published 4 for A, 0 for B). The p-value is reported and is not
   a criterion: it is a function of n and of the discordance, not a property of the apparatus.

The tolerances are wide on purpose, because n = 30 cannot resolve narrower ones. If the control does
not reproduce, the report lists what differs from the first run (model, route, harness commit, date)
before anything is concluded, and the new tasks are not read. The first run's model was not recorded;
the control reads the number on the model used now, and a divergence is reported, not tuned.

## Decision rule

Fixed now. It applies only when the control reproduced and the invariant held.

| Δ (95% t interval, `tost_mean` for the margin) | reading | what happens |
|---|---|---|
| lower bound ≥ −5 pp | **non-inferior** | the default stays; the token saving is then reported as a saving |
| upper bound < −5 pp | **inferior** | goes to the owner with both numbers, recommending the default off |
| interval contains −5 pp | **unresolved** | goes to the owner with both numbers; the default is not called safe |

"Both numbers" means the completion difference with its interval, and the prompt-token cost per
completed run with its ratio, side by side, so the owner decides the trade and the machine does not.
A token saving never offsets a completion loss in the reading: it is the owner's to weigh.

A point estimate inside 5 pp is **not** the criterion, even though it is the shortest reading of "the
default stays if completion does not fall by more than 5 pp". With 30 tasks a point estimate inside the
margin is what an unresolved result looks like most of the time, and a rule the noise can confirm is not
a rule.

## What this would still not say

- Nothing about MCP deferral, the cron, the bot, or a model other than the one named in the result.
- Nothing about tasks that do not need a deferred tool. The first bench's observation (0 of 33
  real calls reached a deferred tool) is a statement about use, and this is a statement about what
  happens when a deferred tool is needed.
- Admission by probe selects tasks A completes. The result bounds selection failure on tasks the
  model can do with the tool, which is the population where deferral could lose something.

## Cost, and what is needed to run it

The first run averaged about 53 thousand prompt tokens per run. 180 main runs, 60 control runs and
the probes (3 + 3 runs per candidate, over a candidate pool larger than 30) come to a few hundred runs
and tens of millions of prompt tokens, on a hosted model. **That is spend, so it needs the owner's go
and a named model.** Until then this file is the whole of the second measurement.

## Order of work

1. This file is committed.
2. Candidates are authored and probed; the admitted list (≥ 30 tasks) is committed with the probe
   counts. Nothing about B has been seen.
3. The control and the main run, one session.
4. `RESULT_2.md`: Δ, interval, the selection-failure signature, the control's three criteria, the
   dropped tasks, and the reading from the table above. This file is not edited.

## `bench/PROTOCOL.md` §11–§14

- **§11, the interval.** Per-task differences, one-sample t (`mean_t_interval`), as in *Statistics
  and power*. McNemar on (task, repeat) pairs is reported for continuity only.
- **§12, the margin.** Non-inferiority, one-sided, at −5 pp, declared above before the first call.
  The reading is the decision table, which uses the two-sided 95% interval — stricter than the
  `1 − 2α` = 90% interval §12 requires as a minimum; `tost_mean(values, margin=0.05)` is printed beside
  it. A spanning interval reads "unresolved", never "no difference".
- **§13, the controls.** *Trivial-agent arm:* the admission probe already runs every task with the
  needed tool denied and admits only tasks that complete 0 of 3, so the floor of each admitted task is
  measured and is zero. *Grader-hijack probe:* the verifier is a shell command over the fixture; before
  the main run, one probe writes a `conftest.py` and a file named like the verifier's expected output
  into the task tree, and the verdict must not move — if it does, the verifier is fixed and the task
  list re-probed. *Random arm at matched cost:* deferral is not a selection mechanism among
  alternatives scored against each other, so there is no random router to match; not applicable.
  *Attack scoring, detection probe, placebo arms, rule-withdrawn arm:* no attack, judge, added prompt
  text or instruction rule is measured here; not applicable.
- **§14, model scope.** One model, named in the result. An *inferior* or *unresolved* reading may lead
  the owner to default deferral **off**, which is a removal; under §14 it does not ship as a default
  change until a second model family is measured with this same registration. Until then the result
  is labelled with the one model it was measured on.
