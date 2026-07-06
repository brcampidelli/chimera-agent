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
