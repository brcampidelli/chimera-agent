# Local weak-model-lift A/B (Docker-free)

An honest, reproducible A/B that measures whether Chimera's scaffolding lifts a **weak/cheap** model
on real, test-graded coding tasks — **without** the official Terminal-Bench (needs Python ≥3.12) or
SWE-bench (needs a Docker evaluation harness). It is a *local proxy* for the same shape of
experiment, and it is labeled as such: these numbers are **not** the official leaderboards.

## The experiment

- **Task set** (`tasks.py`): 8 small Python tasks, each graded by a strict `pytest` file that is the
  ground truth. Six are build-from-scratch (tricky edge cases + multi-part requirements); two are
  bug-fixes inside a small multi-file package (so `--repo-map` is relevant).
- **Two arms, same retry budget — the only variable is the M14 scaffolding:**
  - `baseline` — `chimera solve` with plan + manager + verify-or-revert (3 attempts), **no** M14 flags.
  - `chimera` — the same, **plus** `--repo-map --progress-ledger --replan --checklist`.
- **Verdict is the test's, not solve's.** After each run the harness re-runs `pytest`
  *independently* in the workspace and records that exit code — never solve's self-report.

## Honesty guards

- Both arms run the **same** model, the **same** tasks, the **same** attempt budget. Only the
  scaffolding flags differ, so the delta isolates the M14 contribution (not retries or the model).
- The A/B is scored by `chimera bench-compare`, which reports each arm's Wilson-bounded pass rate,
  the delta, and its Newcombe 95% CI. "Significant" means the CI excludes zero — and with only 8
  tasks the CI is **wide**, so a null result is expected and reported plainly.
- The model is whatever `CHIMERA_DEFAULT_MODEL` is set to (a cheap model — the point of the thesis).

## Run it

```bash
uv run --no-sync python bench/local_lift/run_ab.py --timeout 280
chimera bench-compare bench/local_lift/results/baseline.json \
    bench/local_lift/results/chimera.json --treatment-name chimera
```

The runner is **resumable**: results append to `results/details.jsonl`, and a re-run skips finished
(task, arm) cells. Per-run workspaces land in `results/workspaces/` (gitignored).

## What this does and doesn't prove

It shows whether the scaffolding moves the pass rate on this small local set with a real cheap model
— a directional, reproducible signal. It does **not** substitute for the official benchmarks; the
adapters for those (`chimera.eval.terminal_bench`, `chimera.eval.swe_bench`) are ready and wired to
the same A/B engine for when a Python 3.12 + Docker environment is available.
