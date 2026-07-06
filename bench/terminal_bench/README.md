# Official Terminal-Bench A/B — self-contained runner (disposable box only)

Run the **official** Terminal-Bench 2.0 as an honest A/B — a neutral built-in agent vs Chimera on
the same task IDs and model — and report the delta with a confidence interval via
`chimera bench-compare`. This is the "right regime" the local proxy in `../local_lift/` can't be.

> ⚠️ **Run this on a DISPOSABLE box only** — a throwaway VPS or a local machine with Docker. **Never
> a production host.** Terminal-Bench pulls/builds a Docker image per task on the shared daemon and
> can consume real disk; the scripts include a disk guard, but a live-service box (e.g. a trading
> VPS) is the wrong place for it.

## What's solid vs what you must check

- **Solid + unit-tested here** (`chimera.eval.terminal_bench`): the per-task `chimera solve` command,
  and the in-container bootstrap (`container_bootstrap` / `build_container_command`) that installs a
  self-built chimera wheel into each minimal task container — the fix for the real gap that `chimera`
  does not exist in Harbor's task containers.
- **Version-specific, you must confirm against your installed `terminal-bench`**: the exact `tb run`
  flags (agent selection, `--task-id`, output path), the neutral baseline agent's name, and how the
  agent wheel is made available inside the container. These are surfaced as env-vars at the top of
  `run_ab.sh` with pointers to `tb run --help` — set them once for your TB version.

## Steps

```bash
# 1. Setup (preflight: Docker running, Python >= 3.12, >= 25G free; builds the chimera wheel):
bash bench/terminal_bench/setup.sh

# 2. Provide the key and pick a small task subset + the neutral baseline agent for your TB version:
export OPENROUTER_API_KEY=sk-...
export MODEL=openrouter/deepseek/deepseek-chat-v3.1
export TASK_IDS="task-a,task-b,task-c"      # real Terminal-Bench 2.0 task ids (tb tasks list)
export BASELINE_AGENT="terminus"            # the neutral built-in agent name in your TB version

# 3. Run the two-arm A/B (disk-guarded, cleans up after):
bash bench/terminal_bench/run_ab.sh
```

The run produces `results/baseline.json` and `results/chimera.json` (per-task pass/fail on the same
IDs) and prints the honest delta + 95% CI. The verdict is Harbor's own tests, never self-reported.

## Safety built in

- **Preflight** refuses to start without Docker, Python 3.12+, and `MIN_FREE_GB` (default 25G) free.
- **Disk guard** (`MIN_FREE_GB` during the run, default 15G) aborts + cleans up if free disk drops
  below the floor between tasks — so a runaway image pull can't fill the disk.
- **Cleanup** on exit prunes stopped Terminal-Bench containers and dangling images.
- Everything lives under `bench/terminal_bench/` (venv, wheel, results) — remove the dir to reclaim.

## Honest note

The Python core is tested; the shell glue is a disk-guarded scaffold whose TB-version-specific knobs
are config, not magic. Expect to set `TASK_IDS` / `BASELINE_AGENT` and confirm the `tb run` flags
against your installed `terminal-bench` — then the numbers are real and reproducible.
