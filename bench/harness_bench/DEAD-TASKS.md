# The eleven tasks that told us nothing, and the one that could not have

**2026-09-13.** `bench/irt` found that **11 of the factorial's 23 tasks were answered identically by
all 24 respondents**. This is what each of them actually was. **US$0** — reading results we already
have.

## The eleven are three different things

| | tasks | what it is |
|---|---|---|
| never failed | 016, 045, 051, 064, 083, 084 | too easy for `deepseek-v3.2` at a 0.8 threshold |
| never passed, genuine partial credit | 042 (0.40), 047 (0.73), 086 (0.64), 092 (0.34) | real difficulty, flattened by the binary cut |
| **never passed, and could not have** | **087** | **our grading environment lacked `pytest`** |

Calling all eleven "hard" was never right. Four of the five never-passed tasks scored 0.34–0.73 —
they are partial successes the 0.8 threshold renders as zeros, which is a property of the cut, not
of the tasks. And the fifth is not about difficulty at all.

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
   use them *because of the cut*, not because they are uninformative.
4. **Re-run 087 with pytest installed** before deciding anything about it.
