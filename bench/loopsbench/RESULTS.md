# The first ruler we did not write — and the five runs it took to earn a number

Run `chimera-p5`, 2026-09-07, against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its five
amendments. LoopsBench (arXiv 2608.00267, `microsoft/Loopsbench`, MIT) used **unmodified**:
`--agent-import-path` is an upstream feature, so our agent lives in our repo and nothing is forked.

**Cost: US$ 25.49**, every leg priced, no unpriced estimate anywhere in it.

## The number

| | |
|---|---|
| resolved | **1 of 8** — `task_ml_four_assignments`, **16 of 16 tests passed** |
| as the harness reports it | **12.50%** |
| among tasks that got a verdict | **1 of 6 = 16.7%** (see below) |
| registered prediction | **0/8 to 1/8** |

Two tasks — `task_dbcompiler` and `task_sql_engine_myjql` — carry `is_resolved: null` with no test
results. Their test panes show pytest collecting and failing (`collected 13 items`, `FFF`) and then
cutting off: **the grading phase was truncated, so the harness has no verdict**, and it correctly
declines to invent one. They were failing at the point of truncation, so the honest reading is that
the true rate sits between the two figures and closer to the first. Both are reported rather than
whichever flatters.

## 🔴 What this number is not

**It is not comparable to the published 25.00%.** That figure is Opus-4.7 driving Claude Code across
all 112 tasks. This is `deepseek-chat-v3.1` driving Chimera across 8. **Three** things differ at once
— model, loop, and subset — so any difference measures the bundle. Registered before the run for
exactly this reason, and repeated here because a number and a number on the same page invite the
comparison whether or not it is licensed.

What it does say: **the pair (this model, this loop) is inside this bench's usable range, not below
it.** That alone is what three self-authored suites could never produce — `learning_lift` hit
84–92% across three attempts to build a 40–60% band, and `terminal_bench` floored at 7.5% / 2.5%
with 37 of 40 failing both arms. A ruler with no middle cannot answer whether the loop helps. This
one has a middle, and we are in it.

## The apparatus, which is most of what happened here

**Five runs. Four discarded. Every one of them for a defect that produced `Pass Rate: 0.0%` —
exactly what a loop that cannot do the work looks like.** Not one was reported as a result, and not
one was announced by the harness: each was found by opening the artifact of a failure instead of
reading the table above it.

| run | discarded because | how it was found |
|---|---|---|
| p1 | `--break-system-packages` on pip 22 — the option did not exist until pip 23 and older pip *hard-fails* on it | the failed task's artifact was `chimera_install-failed.log`, not a solve log |
| p2 | `uv venv` refuses an existing environment, so the outer loop's rounds 2–3 died: **one round of agent work instead of three** | a task carrying *both* an install-failure log and a solve log with real content |
| p3 | **10 of 21 rounds had no shell** (`CHIMERA_HOST_EXEC=ask`, no bubblewrap, no TTY) and **9 of 21 had no model** (key hit its ceiling mid-run) | reading a solve log with actual eyes |
| p4 | **20 of 23 rounds** logged `trabalho revertido`: verify-or-revert rolled the tree back before the tests ran | checking whether the work reached the grader at all |
| **p5** | — | **24 of 24 rounds with a model and a shell, 0 install failures** |

⭐ **The p4 defect is the one worth keeping.** It was not a bug in the adapter. `--keep-workspace`
exists in Chimera for exactly this, and its help says so: *"On failure, leave the last attempt's
edits on disk **for an external grader** (don't revert)."* LoopsBench **is** the external grader, and
the flag was not passed. **`bench/terminal_bench` does not pass it either**, so the 7.5% / 2.5% in
that scoreboard was measured with the same handicap and should be re-read in that light.

⭐⭐ **And the metric that was not a metric.** Across p3 and p4 the revert was counted by grepping
`trabalho revertido` out of the solve log. The paired test that finally settled the flag — same task,
same model, same image, failure forced with `--verify false`, only the flag changed — showed **both
arms log that line**. The revert happens either way; the flag restores afterwards. The signal is
whether the file survives, and it is the opposite of what the log line suggested:

| arm | loop verdict | `proof.txt` |
|---|---|---|
| without `--keep-workspace` | failed, "reverted" | **absent** |
| with `--keep-workspace` | failed, "reverted" | **`HELLO_FROM_AGENT`** |

A quantity read off a line that both outcomes produce is not a measurement. It took a paired test to
see that, which is the same lesson this project has now learned in four different costumes.

## The arm

| | |
|---|---|
| agent | `chimera_loopsbench_agent:ChimeraAgent` (this directory, `--agent-import-path`) |
| model | `openrouter/deepseek/deepseek-chat-v3.1` |
| flags | `--repo-map --progress-ledger --checklist --max-attempts 1 --max-steps 60 --no-remember --no-collect --no-evolve-skills --no-manager --keep-workspace` |
| install | always a private venv via `uv` — three different images broke three different ways installing into the system Python |
| isolation | `CHIMERA_HOME=/chimera/home` per container; nothing carries between tasks |
| concurrency | 2 · attempts 1 · 3 outer rounds per task (the harness's own loop) |

`--max-steps 60` rather than the shipped default of 8: eight tool calls cannot finish a multi-module
implementation behind a dependency DAG under any loop, and leaving it would have measured the step
limit. `--no-manager` because with one attempt the reviewer cannot do the thing it is for — feedback
on a retry — so its only remaining effect is a prose veto over an artifact, which this codebase's own
`autonomous.py` says must not happen; it also puts this arm on the same footing as every built-in
LoopsBench agent, none of which has an internal reviewer.

## The subset, and what "easy" means here

Eight tasks, chosen before any result by rule: `difficulty == easy`, ordered by
`(max_agent_timeout_sec ASC, task_id ASC)`, first 8 of the 17 easy ones. The dataset is **52 hard /
43 medium / 17 easy**, and "easy" still means two-hour budgets and 5–24 separately tested units.

| task | budget | units | verdict | wall clock | cost |
|---|---:|---:|---|---:|---:|
| `task_ml_four_assignments` | 7200s | 10 | **RESOLVED 16/16** | 33.5 min | $5.46 |
| `task_compiler_fdmj_llvm` | 7200s | 7 | failed, tests ran | 30.2 min | $2.77 |
| `task_cs61b_extra_java_bundle` | 7200s | 22 | failed, tests ran | 26.2 min | $1.35 |
| `task_os_c_fs_labs` | 5400s | 7 | failed, tests ran | 43.9 min | $3.72 |
| `task_xjqkl` | 3600s | 6 | failed, no test detail | 22.9 min | $5.25 |
| `task_db_storage_index_labs` | 1800s | 10 | failed, no test detail | 24.1 min | $3.41 |
| `task_dbcompiler` | 7200s | 24 | **not graded** (tests truncated) | 44.2 min | $3.54 |
| `task_sql_engine_myjql` | 1800s | 5 | **not graded** (tests truncated) | 29.8 min | — |

`task_sql_engine_myjql` produced no receipt block, so its spend is not in the total: **US$ 25.49 is a
floor, not the full cost.** Reported as a floor rather than padded with an estimate.

## What this cannot show

- **n = 8.** The Wilson interval on 1/8 is roughly ±30 pp. This is a pilot whose job was to decide
  whether a larger run is worth paying for. It is not a rate anyone should quote.
- **One model, one loop, one subset, one seed.** No arm was repeated, so nothing here separates the
  result from run-to-run variance — a lesson this project has paid for twice (§2m, §2x).
- **The eight easiest tasks in the set.** Nothing here speaks to the 52 hard ones.
- **Two of eight were not graded.** The denominator is honestly ambiguous, and both readings are on
  the table above rather than one of them being chosen.
- **No A/B.** This measures Chimera on LoopsBench; it does not measure what Chimera's scaffolding
  contributes, which needs a bare-agent arm on the same tasks with the same model.

## The repeat — same rate, different task, and it kills the A/B at this n

Run `chimera-p6`, registered before it ran as the variance check: `p5` again, verified identical by
diff except the run-id. Both apparatus-clean — **24 and 22 rounds, zero install failures, zero
rounds without a model or a shell.**

| | p5 | p6 |
|---|---|---|
| resolved | **1 of 8** | **1 of 8** |
| accuracy | 12.50% | 12.50% |
| which one | `task_ml_four_assignments` | `task_sql_engine_myjql` |

**The rate is identical and the overlap is zero.**

| | count |
|---|---:|
| passed in **both** runs | **0** |
| passed only in p5 | 1 |
| passed only in p6 | 1 |
| failed in both | 6 |
| **discordant between two identical runs** | **2 of 8 = 25%** |

`task_ml_four_assignments` passed 16/16 tests in p5 and failed in p6. `task_sql_engine_myjql` was
one of the two ungraded rows in p5 and passed in p6. Nothing about the configuration changed.

⭐ **A stable rate hiding an unstable outcome is the worst shape for a paired comparison**, and this
is exactly why the A/B was not run first. A scaffolding-on/off test compares tasks pairwise; with
25% of pairs flipping on their own, the discordant column — the only column McNemar reads — is
noise. Sizing it crudely against that noise floor:

| effect to detect | tasks per arm |
|---|---:|
| +10 pp | ~97 |
| +15 pp | ~43 |
| +20 pp | ~25 |

(normal approximation over discordant pairs; an order of magnitude, not a plan)

An A/B at n=8 could not have detected anything short of a 30-point effect, and would have returned
"one task different" — which reads as a result and is not one. **The 8-task pilot cost US$ 25 and
bought the knowledge that the experiment everyone wanted needs 25–97 tasks per arm.** LoopsBench has
112, so that is affordable in the full set and was not affordable in the subset.

⚠️ **Two runs alert, three decide** (§2x). The 25% is itself an estimate from two samples; what it
supports is "n=8 is hopeless", not a variance figure anyone should carry into a power calculation
without a third run.

⭐ **One thing did stabilise:** six of eight tasks failed in both runs, and the three that failed
earliest in both were the same three. The floor is consistent; the ceiling is a coin flip. That
pattern says the discriminating middle on this subset is roughly **two tasks wide**, which is the
real reason n=8 has no power — not the rate, the width of the band.

## What is worth doing next, in order

1. **Re-read `bench/terminal_bench` in light of `--keep-workspace`.** Its 7.5% / 2.5% was measured
   with the loop discarding its own work before the grader looked. That scoreboard may be wrong in a
   direction that flatters nobody.
2. **A paired arm on these same 8 tasks** — same model, scaffolding on versus off. That is the
   question the project actually wants answered, and this run built the apparatus for it.
3. **Fix the grading truncation** on the two ungraded tasks, or report them as excluded by rule.
