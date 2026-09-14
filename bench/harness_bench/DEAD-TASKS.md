# The eleven tasks that told us nothing, and the one that could not have

**2026-09-13.** `bench/irt` found that 11 of the factorial's 23 tasks were answered identically by
all 24 respondents **once binarised at 0.8**. This is what each of them actually was. **US$0** —
reading results we already have.

> ⚠️ **Corrected the same day.** The threshold is `bench/irt`'s, not the factorial's:
> `read_results.py` computes its main effects on the **continuous** score over all 23 tasks
> (`n_tasks=23`). **Nine of these eleven vary continuously** and did contribute to #453's numbers.
> Only 016 and 083 are genuinely flat. See the retraction at the top of `bench/irt/RESULTS.md`.
> What is unaffected is §087 below, which is about the grader rather than the cut.

## The eleven are three different things

| | tasks | what it is |
|---|---|---|
| never failed | 016, 045, 051, 064, 083, 084 | too easy for `deepseek-v3.2` at a 0.8 threshold |
| never passed, genuine partial credit | 042 (0.40), 047 (0.73), 086 (0.64), 092 (0.34) | real difficulty, flattened by `bench/irt`'s cut — not by #453's analysis |
| **never passed, and could not have** | **087** | **our grading environment lacked `pytest`** |

Calling all eleven "hard" was never right, and calling them uninformative was wrong too. Four of the
five never-passed tasks scored 0.34–0.73 — partial successes the 0.8 threshold renders as zeros,
which is a property of the cut and not of the tasks, and #453 never applied that cut to its effects.
The fifth is not about difficulty at all.

## 087 was unpassable in our environment

Its grader runs

```python
subprocess.run([sys.executable, "-m", "pytest", "tests"], cwd=project, ...)
add("pytest", result.returncode == 0, 0.25, ...)
```

`sys.executable` is `~/hb-venv/bin/python`, and **that interpreter has no pytest**. The check
returned non-zero on **24 of 24 runs**, every time carrying the detail
`No module named pytest`, and 0.25 of the 0.95 available weight was scored as the agent's failure.

The best score any arm reached on it was **0.675** against a **0.8** threshold, so no run could have
crossed. Meanwhile the task's own behavioural check — `hidden_cli_behavior`, weight 0.35 — passed
with score **1.0**. The agent did the work; the grader could not check it.

**A missing module did not fail loudly. It failed as a low score, which reads as a hard task**, and
sat in the factorial as one of the eleven that "could not distinguish any arm".

### Answered: re-graded with pytest installed, US$0

`regrade_087.py`. The run's sandboxes survive on disk, so the agent's work was not paid for twice —
the very directories the original left behind were re-graded by the same grader in a fixed
environment. The agent's work is unchanged and only the grader's environment differs.

| | mean | max | passing (≥0.8) |
|---|---:|---:|---:|
| as recorded | 0.6050 | 0.6750 | **0 of 24** |
| re-graded | 0.8030 | 0.9250 | **19 of 24** |

**The agent solved it in 19 of 24 runs and the factorial recorded 0.** The five that still fail
pytest fail it for real — so the task *discriminates*, which makes it one of the more informative
items in the suite rather than a dead one. It was never a hard task. It was an unpassable one.

### And what it does to #453's published effects: almost nothing

`effects_with_087_corrected.py` re-runs `read_results.py`'s own analysis with the 24 corrected cells
overlaid. Nothing is written back into the harness's result files — the record of what the run
produced stays as it was, and this is the corrected reading of it.

| effect | published | with 087 corrected |
|---|---|---|
| A repo-map | −0.012 [−0.036, +0.010] | −0.013 [−0.037, +0.009] |
| B checklist | +0.005 [−0.052, +0.052] | +0.006 [−0.051, +0.053] |
| C planner | +0.003 [−0.023, +0.029] | +0.002 [−0.023, +0.028] |

Every interval still spans zero and no point estimate moves by more than 0.001. **Both halves are
true and neither cancels the other**: the defect was serious for reading *that task*, and it was
roughly balanced across arms, so the paired comparison barely felt it. #453's null stands, now on
a corrected 087.

## A worry raised and retired

The same results showed `'rubric': {'skipped': True, 'reason': 'no proxy trace: missing responses/'}`
on **552 of 552** solves — an LLM-rubric component of the oracle switched off for the whole run.
That looked, briefly, like a systematically suppressed score.

It is not. `outcome_llm_weight` is **0.0 on all 552**, and no `task.yaml` in the suite declares an
llm or rubric weight. The skipped component carried zero weight, so nothing was suppressed. Recorded
because "we checked and it was fine" is worth as much as a finding, and costs the next person the
same search.

*(And one correction to my own first reading: 087's `task.yaml` lists `pytest` under `tags:`, not
under a requirements field. The task does not declare the dependency — its grader simply uses it.
I nearly wrote the stronger, false version.)*

## What ships: the preflight §2c already required

`bench/harness_bench/preflight.py`. For every task's `oracle_grade.py` it extracts each module
invoked as `[sys.executable, "-m", "<module>"]` and each bare executable, then checks the former
imports in the grading interpreter and the latter is on PATH.

Run against the real suite, 106 graders, seconds, offline:

```
modules invoked with -m (2):
   MISSING csvtool.cli
   MISSING pytest
executables invoked (3):
   ok  git      ok  node      ok  pytest

!! 2 task(s) whose GRADER cannot run as written:
   087-cli-parser-bug-tests             missing csvtool.cli, pytest
   088-api-contract-mock-client-compat  missing pytest
```

087 is the one that cost us. 088 was already dropped by #453's Amendment 2 for a different missing
prerequisite — which is its own small point: **the two tasks this catches are the two that went
wrong**, one of them caught at the time by a stop rule and one of them not caught at all.

Note `pytest` shows **ok as an executable and MISSING as a module**: it is on PATH from another
environment and absent from the grading venv. The grader uses `sys.executable -m`, so the one that
matters is the module. A preflight that only asked `which pytest` would have said everything was
fine.

`csvtool.cli` is the ambiguity the tool declares rather than guesses: it is 087's *deliverable*,
correctly absent before the agent works. Distinguishing that from a real dependency needs the task's
intent, so both are reported and the message says which question to ask.

This is Bee §2c #4 — *run every reference before loading any model; if the reference does not run,
the defect is the evaluator's* — which this project wrote down, put in `PROTOCOL.md`'s checklist,
and then did not execute before spending US$29.

## What it changes for a future factorial

1. **Run the preflight first.** It is free and it would have caught this.
2. **Replace the six never-failed tasks**, which cost money and moved nothing.
3. **Do not treat the four genuine partials as dead.** At a threshold of 0.8 they are constants; the
   information in 0.34 against 0.73 is real and the binary cut throws it away. `bench/irt` could not
   use them *because of the cut it chose* — the factorial itself used them fine.
4. ~~**Re-run 087 with pytest installed** before deciding anything about it.~~ **Done** — see
   above. 19 of 24, and #453's effects move by ≤0.001.
