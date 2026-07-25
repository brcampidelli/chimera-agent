# Benchmarks — proving the weak-model lift

Chimera's thesis is that structure makes a **weak/cheap** model punch up. The honest way to show
that is a controlled A/B on a standard benchmark: fix the task subset and the model, make the
**only** variable the scaffolding, and report the delta with a confidence interval — not a bare
"it got better". (Independent research finds the same model swings ~7pts from scaffolding alone,
so an unqualified score says nothing about *your* contribution.)

## The experiment

**Benchmark:** [Terminal-Bench 2.0](https://www.tbench.ai/) — Docker task + instruction +
verification tests, graded pass/fail by those tests, driven by the agent-agnostic **Harbor**
harness.

- **Arm A (baseline):** one free model in Harbor's neutral scaffold — "weak model alone".
- **Arm B (treatment):** the **same** model, the **same** task IDs, driven by Chimera.
- **Metric:** pass@1. **Headline:** Δ = rate(B) − rate(A), with a 95% CI.
- **Honesty guards:** pin the task-ID subset (publish it), run ≥3 seeds, publish all transcripts,
  and add a frontier-model row only as a *ceiling reference* — never as the comparison.

The one number that proves the thesis: **free model alone = X%, free model + Chimera = Y%, same
tasks, Y ≫ X.**

## Running it

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

Chimera plugs in as the treatment agent via `chimera/eval/terminal_bench.py`
(`make_chimera_tb_agent(model)` builds a Harbor `BaseAgent` that runs `chimera solve` with the
scaffolding flags). Point Harbor at a pinned subset and a free model for each arm; see the
[Harbor docs](https://www.tbench.ai/) for the exact `harbor run` invocation and `--agent-import-path`.

## SWE-bench Verified (the second scoreboard) — **run, twice**

Terminal-Bench proves the thesis on CLI tasks; SWE-bench proves it on real GitHub bug-fixes — given
a repo at a base commit and an issue, the agent must produce a patch that makes the instance's
`FAIL_TO_PASS` tests pass while keeping `PASS_TO_PASS` green. "Verified" is the human-validated
subset.

### Results

Two pre-registered runs on the same frozen 19-instance `django/django` slice (easiest difficulty
stratum), `deepseek-chat-v3.1`, pass@1, graded **only** by the official `swebench` 4.1.0 harness in
Docker. Full write-up: [`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| run | baseline | + Chimera | paired Δ | 95% CI | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | not significant |
| 2 (`max_steps=30`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | not significant |

Run 1 is an **exact zero** and is published unchanged. Run 2 fixed two faults that were *ours* — the
scaffold ran without its strongest mechanism, and 8 tool-calling steps is not enough to navigate a
250 MB repository — and came out **3 instances won, 0 lost**. The pair is the finding: the scaffold is
worth *nothing* when the agent is starved of steps and *three instances* when it is not, and it wins
by editing **better** (69% vs 57% precision when it edits), not by editing more.

> ⚠️ **57.9% is not a SWE-bench Verified score.** The slice is deliberately easy and single-repo,
> chosen so a paired A/B has room to measure; a real Verified score needs the full 500. And the delta
> is **not significant** — with 8 both-fail pairs, n=19 leaves only three informative pairs.

Run 2 also ships a **retraction**: the mechanism we had traced for run 1's empty patches was wrong
(the fix was the step budget, not the diff-gate we blamed), corrected as prominently as it was claimed.

### The adapter

The adapter (`chimera.eval.swe_bench`) is honest about its boundary: the pure parts — the per-
instance `chimera solve` invocation (treatment arm) and the parsing of the official evaluation
report — live here and are unit-tested; the dataset and the Docker evaluation harness are **opt-in
and not bundled**, and the pass/fail verdict comes from SWE-bench's own tests, never self-reported.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Both reports are projected onto the shared instance list (a missing id counts as unresolved), so
the two arms are always compared on identical instances — then the same Newcombe-CI verdict applies.

## Scoring the A/B (no benchmark needed)

Once each arm has produced per-task pass/fail, the stats are one command — this needs **no
extra**, so the honest-reporting engine is always available:

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Each file is a JSON list of booleans (or `{task_id: bool}`) over the **same** task IDs. Output:
each arm's Wilson-bounded pass rate, the delta, its Newcombe 95% CI, and whether the difference
is **significant** (the CI excludes zero). If it isn't significant, that's reported plainly — a
larger subset / more seeds, or the feature genuinely doesn't move the number.

This same `bench-compare` is the measuring stick for every later feature: each M14 addition must
show it moves Δ on the identical subset, or it's cut.

## The honest trap (what to avoid)

- **Contamination** — public SWE-bench has documented solution leakage; prefer contamination-
  resistant sets and report the caveat.
- **Scaffold confound** — never report a raw "we scored X%"; only the A/B delta isolates
  Chimera's contribution.
- **Wrong baseline / cherry-picking** — compare weak+Chimera to the *same weak model alone*, on
  the *identical* task IDs, with seeds and full logs. A frontier model is a ceiling, not a rival.
