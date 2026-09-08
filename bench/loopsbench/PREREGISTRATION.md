# LoopsBench — the first ruler we did not write

Registered **2026-09-06, before any task ran**. Amendments are appended with what had already been
seen when they were written.

## Why this bench

Every suite this project authored to measure the loop has failed to produce a discriminating middle,
from one side or the other:

| suite | outcome | the problem |
|---|---|---|
| `bench/learning_lift` | 84-92% across **three** attempts to build a 40-60% band | ceiling — everything passes |
| `bench/terminal_bench` | 7.5% / 2.5%, **37 of 40 failing both arms** | floor — nothing passes |

A ruler with no middle cannot say whether the loop helps, in either direction. LoopsBench
(arXiv 2608.00267, `microsoft/Loopsbench`, MIT) is the first one we did not author: 112 tasks, 8
languages, median dependency depth 6, and a published best of **25.00%** (Opus-4.7 + Claude Code +
outer continuation). It is used **unmodified** — `--agent-import-path` is an upstream feature, so our
agent lives in our repo and nothing is forked.

## 🔴 What this run cannot answer, stated first

**Our number will not be comparable to the published 25.00%.** That figure is Opus-4.7 driving Claude
Code. This run is `deepseek-chat-v3.1` driving Chimera. Two things differ at once, so any difference
measures the pair, not the loop — the §2g error this project has made four times in two days
(*same items? same ruler? same n?*). Anyone who reads a number here against 25% is reading the model.

What it **can** answer, and all it can answer:

1. Does the harness run our agent end to end on this machine — install, solve, grade?
2. At this model, on these 8 tasks, is the loop **above the floor at all**?
3. Is the plumbing in place for a real paired A/B later, where model and bench are held fixed and
   only the loop varies?

## Phase 0 — the oracle, before any model is loaded

`--agent oracle` applies each task's own reference solution. **If the oracle does not resolve a task,
the defect is in the apparatus and no model number from it means anything.** This is the guard that
caught a closed-world evaluator in a previous project, where 35 of 85 references were impossible by
construction and the measured rate read 23.5% against a real 57.6%.

**Gate: the oracle must resolve ≥ 1 of the pilot tasks before a single paid task runs.** If it
resolves none, this document records that and the phase stops at US$ 0.

## Phase 1 — the arm

One arm. Not an A/B: with no idea yet whether we clear the floor, a second arm doubles the cost of
finding out that neither moves.

| | |
|---|---|
| agent | `chimera_loopsbench_agent:ChimeraAgent` (this directory, on `PYTHONPATH`) |
| model | `openrouter/deepseek/deepseek-chat-v3.1` |
| flags | `--repo-map --progress-ledger --checklist --max-attempts 1 --no-remember --no-collect --no-evolve-skills` |
| attempts | 1 |
| isolation | `CHIMERA_HOME=/chimera/home` per container — no memory or skill carried between tasks, so the 8 rows stay independent |

`--max-attempts 1` deliberately: the retry lever is the thing a later A/B should vary, and leaving it
at 1 here keeps this run inside the per-task budget with no timeout confound. Same reasoning, and the
same setting, as the Terminal-Bench arms.

### Amendment 1 — written after Phase 0 passed, before any paid task ran

Two defaults in the arm above would have guaranteed the predicted 0/8 **for reasons that have
nothing to do with the loop**, which is the §2q failure: an instrument that cannot exhibit the effect
produces no evidence about it. Both are changed here, before spending, and the change is recorded
rather than made quietly.

1. **`--max-steps 8` → `60`.** `chimera solve` defaults to eight tool-calling steps. LoopsBench is
   explicitly a *long-horizon* benchmark — multi-module implementations behind a dependency DAG, with
   two-hour budgets. Eight steps cannot finish one of these tasks under any loop. Terminal-Bench
   comparability was the reason to leave it alone; measuring on *this* bench is the reason not to.
2. **The solve's inner timeout no longer binds.** The adapter shipped with a 1500 s cap, which on a
   7200 s task would have truncated the arm at 21% of its allowance and called the result a miss. It
   is raised so the **task's own `max_agent_timeout_sec` is the binding constraint**, minus 60 s to
   leave room to write the log out. The mirror of the Terminal-Bench rule *"never silently inflate
   the budget"* — never silently deflate it either.

Concurrency is **2**. Not higher: the Terminal-Bench run at 5 produced a result flip on a trivial
task that took a controlled serial repeat to attribute to variance rather than contention. Not 1:
eight tasks at their full budgets is 11.5 hours of wall clock.

### Amendment 2 — the first run was discarded, and why

Written **after seeing** the first trial of the paid run fail and reading its log; the run was
stopped at 12.5% and its numbers are not reported anywhere as a result.

`task_compiler_fdmj_llvm` came back failed. The artifact beside it was `chimera_install-failed.log`,
not a solve log — so the failure was **`agent_installation_failed`, not a miss**. Two defects were
stacked in it, and both were mine:

1. **`--break-system-packages` on pip 22.** That option arrived in pip 23; older pip treats it as an
   unknown option and exits, rather than warning. The image ships Python 3.10.12 / pip 22.0.2, so
   every install there died on the flag. Fixed by asking pip what it supports before passing it.
2. **The one the fix uncovered: `chimera-agent` requires Python ≥ 3.11** and that image has 3.10, so
   even with the flag gone PyPI correctly answers *"no matching distribution"*.

**The second one had a tempting wrong answer: drop the incompatible tasks.** That would have
selected the subset toward containers that happen to carry a new interpreter — a selection nobody
registered, on the one bench in this project whose entire value is that we did not choose its tasks.
The arm brings its own interpreter instead: `pip install uv` (from PyPI — `astral.sh` answers
urllib with HTTP 403 and these images have no curl, measured both ways in the image that failed),
then `uv venv --python 3.12` and install into it.

**Verified by running the adapter's own `_INSTALL` string** — the one that ships, not a
reimplementation — in both image shapes: the 3.10 image reports `CHIMERA_INSTALL_OK_UV`, the 3.12
image `CHIMERA_INSTALL_OK_SYSPY`, and both `CHIMERA_LAUNCHER_OK`.

⭐ **The lesson this run is now carrying:** the harness told the truth and the summary did not. The
status board read `Failed: 1 · Pass Rate 0.0%`, which is exactly what a genuine miss looks like. Only
the per-trial artifact name distinguished them. **A pass rate that pools installation failures with
misses is measuring the adapter and reporting it as the loop** — the same shape as the closed-world
evaluator that read 23.5% where the truth was 57.6%. `RESULTS.md` will report the two separately,
and any task that ends in an install failure is excluded from the denominator and named.

### Amendment 3 — the second run was discarded too, for a defect the pass rate could not show

Written after reading the artifacts of run `chimera-p2` at 75% (6 failed, 0 passed) and before its
replacement `chimera-p3` was launched. `p2` is discarded and reported nowhere as a result.

The triage Amendment 2 promised turned up something that did not fit: four tasks carried **both** a
`chimera_install-failed.log` and a `chimera_solve.log` with real content. The directory layout
resolved it:

```
round-01/chimera_solve.log            <- the agent ran, and produced diffs
round-02/chimera_install-failed.log   <- the next round died
```

**LoopsBench's outer loop re-invokes the agent in the same container** — round-01, 02, 03 against one
workspace — so the install script runs each round, and `uv venv` refuses an existing environment
(*"A virtual environment already exists"*). On the four tasks whose image ships Python < 3.11, the
agent therefore worked **one round instead of three**.

⭐ **The reason this is the most important entry in this document:** the shortfall was **invisible in
the metric**. `Pass Rate: 0.0%` reads identically whether the agent had one round or three, and the
run would have been published as evidence that the loop cannot do these tasks when half the arm had
been cut to a third of its budget. Nothing errored, nothing looked wrong, and the number was right
there on the status board — the same family as every §2 defect in the project's lessons file: *dado
some, nada reclama, o número sai*.

Fixed by making the install **idempotent**: if `/chimera/run --help` already works, exit 0 without
reinstalling. Better than `uv venv --clear`, which would re-download a 32 MB interpreter every round.
**Verified the way the defect appears** — the shipped `_INSTALL` run twice in one container, both
image shapes: round-01 `OK_UV` / `OK_SYSPY`, round-02 `ALREADY_PRESENT`, rc=0 throughout.

Two runs are now discarded for adapter defects, neither of which the harness reported as anything
but a failed task. That is the cost of the rule this project keeps: **a number whose apparatus has
not been checked is not a result**, and checking it is what found both.

### Amendment 4 — run p3 discarded; the agent had neither a shell nor, for part of it, a model

Written after run `chimera-p3` completed 0/8 and its artifacts were read. `p3` is discarded.

Two independent things were wrong, and **the summary table showed neither**:

| | rounds |
|---|---|
| **no shell** — Chimera refused to execute the agent's commands | 10 of 21 |
| **no model** — the provider key hit its spend ceiling mid-run (HTTP 403) | 9 of 21 |
| **rounds with both a model and a shell** | **3 of 21** |

`task_db_storage_index_labs` and `task_sql_engine_myjql` ran all three rounds with **no model at
all**; `task_dbcompiler` ran all three unable to execute a single command.

**The shell one is the instructive defect.** `CHIMERA_HOST_EXEC` defaults to `ask`; task images have
no bubblewrap, so there is no OS sandbox, and a container has no TTY to answer the question — so
Chimera correctly refused to run anything. On a benchmark whose tasks are *"build with `make all` and
pass the tests"*, the agent could edit files and execute nothing, and the model still wrote confident
completion summaries the verifier contradicted. Set to `allow`, which is right **here and nowhere
else**: the container is the sandbox and is destroyed after grading.

**A third thing surfaced while proving the fix.** With execution restored, the agent ran the command
and wrote the file — and Chimera's own verify-or-revert **reverted the work**, so the grader saw an
empty tree. Without a `--verify` command the loop falls back to the Manager's judgement of prose,
which `autonomous.py` itself says must not veto artifacts. With `--max-attempts 1` the Manager cannot
do the one thing it is for — feedback on a retry, because there is no retry — so its only remaining
effect is that veto. `--no-manager` is added, and it also puts this arm on the same footing as every
built-in LoopsBench agent: Claude Code and Codex have no internal reviewer reverting work either, and
the benchmark's own tests are the judge.

**Positive proof, not the absence of a warning.** The fixes were verified with a real solve in a task
container on a task whose answer cannot be guessed — *run `uname -sr` and write its output to a
file*. Result: `rc=0`, `success after 1 attempt(s)`, and `proof.txt` containing
`Linux 6.6.87.2-microsoft-standard-WSL2`, the exact string. The command ran **and** the artifact
survived to be graded.

⚠️ **Four runs, four discarded, none of them reported as a result — and the harness never
complained once.** `Pass Rate: 0.0%` is exactly what a loop that cannot do the work looks like.
Every one of the four defects was found by opening the artifact of a failure instead of reading the
table above it. That is the whole cost, and the whole justification, of the rule this project keeps.

### Amendment 5 — run p4 discarded: the loop threw the work away before the grader looked

Run `chimera-p4` finished **0/7** (one task excluded, install) with the apparatus finally clean:
**23 of 23 rounds had both a model and a shell**, zero install failures, zero refusals. And it still
cannot be reported, for a reason that is not a bug in the adapter at all.

**Chimera's verify-or-revert rolled the tree back before LoopsBench ran the tests.** The flag for
exactly this exists, and its own help says so: *"On failure, leave the last attempt's edits on disk
**for an external grader** (don't revert)."* LoopsBench **is** the external grader. It was not
passed. `bench/terminal_bench` does not pass it either, so the omission is older than this run and
may have shaped that scoreboard too.

**Proved by a paired test, because the first attempt to prove it proved nothing.** A single run with
the flag came back `success`, so nothing was reverted and the flag never executed — a green result
that tested the wrong path. The real test forces failure with `--verify false` and runs the same
task twice, changing only the flag:

| arm | loop verdict | `proof.txt` |
|---|---|---|
| without `--keep-workspace` | failed, reverted | **absent** |
| with `--keep-workspace` | failed, reverted | **`HELLO_FROM_AGENT`** |

⭐ **And it corrected how the previous amendment counted.** Both arms log `trabalho revertido` — the
revert happens either way and the flag restores afterwards. So the marker I had been counting across
runs was never the signal; whether the file survives is. A metric read off a log line that both
outcomes produce is not a measurement, and it took a paired test to see that.

### Run 6 — the repeat, registered 2026-09-07 before it ran

`chimera-p5` gave **1 of 8**. Nothing in this project knows how much that number moves on its own,
because no arm has ever been run twice here. Run 6 is `p5` again — same tasks, same model, same
flags, same concurrency, new run-id — and it exists to answer one question: **how big is the noise?**

There is no seed to fix. LoopsBench does not expose one and the model samples above zero
temperature, so "a second seed" means a second identical run and the variation is whatever the pair
(model, loop) produces on its own.

**Why this before the A/B everyone wants.** At 1/8, a scaffolding-on/off comparison would resolve to
"one task different", and one task is not distinguishable from nothing at n=8. §2m cost this project
a gate that could not decide because its threshold sat below the apparatus's own variance; §2x
showed two runs produce a *difference*, not an estimate of variance. This run buys the number that
sizes every comparison after it.

**Registered prediction: 0 to 2 of 8.** Binomial noise alone at p≈0.125, n=8 has a standard
deviation of about 0.94 tasks, so a repeat landing anywhere in 0–2 is consistent with no change at
all. **A repeat at 3+ or a second run resolving a *different* task would be the interesting
outcome** — the first would say the estimate is unstable, the second that which task wins is close
to a coin flip.

**What it cannot do:** two runs alert, three decide. Whatever comes back, the honest output is a
range and an order of magnitude, not a variance estimate. If the two disagree by more than one task,
the third run stops being optional.

## The subset — 8 tasks, chosen by rule

**Rule, declared before looking at any result:** `difficulty == easy`, ordered by
`(max_agent_timeout_sec ASC, task_id ASC)`, take the first 8. Timeout ascending because a 30-minute
task and a 2-hour task are not the same experiment; alphabetical breaks ties so no task was picked
for looking winnable.

| # | task | budget | units |
|---|---|---:|---:|
| 1 | `task_db_storage_index_labs` | 1800s | 10 |
| 2 | `task_sql_engine_myjql` | 1800s | 5 |
| 3 | `task_xjqkl` | 3600s | 6 |
| 4 | `task_os_c_fs_labs` | 5400s | 7 |
| 5 | `task_compiler_fdmj_llvm` | 7200s | 7 |
| 6 | `task_cs61b_extra_java_bundle` | 7200s | 22 |
| 7 | `task_dbcompiler` | 7200s | 24 |
| 8 | `task_ml_four_assignments` | 7200s | 10 |

There are 17 `easy` tasks in the set; these are the 8 cheapest by budget. **"Easy" is the
benchmark's word, not a claim of ours** — the median task here still carries a two-hour ceiling and
5-24 separately-tested units, and the dataset is 52 hard / 43 medium / 17 easy.

## Prediction, registered before running

**0/8 to 1/8 resolved.** Reasoning, not hedging: the same model on Terminal-Bench resolved 7.5% of 40
tasks with 37 failing both arms, and LoopsBench tasks are substantially larger (multi-module
implementations behind a dependency DAG) with a published ceiling of 25% for a *frontier* model on a
mature loop.

**A 0/8 is a real outcome and will be published as one.** It would say the pair (this model, this
loop) is below this bench's usable range — which is information about where the instrument can be
pointed, and is exactly the thing three self-authored suites could not tell us. It would **not** say
the loop does not work, and this document exists so that reading cannot be made later.

n=8 gives a Wilson interval of roughly ±30pp. This is a **pilot**, and its job is to decide whether a
larger run is worth paying for — not to produce a rate anyone should quote.

## Cost

Phase 0 is US$ 0 (the oracle calls no model). Phase 1 spends real money on `deepseek-chat-v3.1`; the
wall-clock ceiling of the 8 budgets summed is **11.5 hours** if every task exhausts its limit, less
under concurrency. The per-task spend is not known in advance and will be reported as measured, per
task, in `RESULTS.md` — including the tasks that spent money and resolved nothing, which is the half
a scoreboard is tempted to leave out.

## What gets published

`RESULTS.md` beside this file, whatever the outcome, with: the oracle's verdict, the per-task rows,
the failure mode of every unresolved task (a harness `agent_installation_failed` and a genuine miss
are different facts), the measured spend, and the log of at least one failure read with actual eyes —
because a rate with no read log is a number with no diagnosis.
