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

## Proven end-to-end (2026-07-06, WSL Ubuntu + Docker Desktop)

The official harness was actually run here, and the Chimera-agent-in-container gap was closed for
real. `chimera_installed_agent.py` is the working `AbstractInstalledAgent`:

1. **Build a wheelhouse** matching the task container's Python/arch (terminal-bench-core is mostly
   the `python-3-13` image → cp313 linux, binary-only so no compiler is needed in-container):
   ```bash
   uv build --wheel -o dist                              # chimera wheel (from the repo)
   uv venv wh313 --python 3.13 --seed
   wh313/bin/pip download dist/chimera_agent-*.whl -d wheelhouse
   cp dist/chimera_agent-*.whl wheelhouse/
   tar cf wheelhouse.tar -C wheelhouse .                 # 65 wheels, ~40 MB, no sdists
   ```
2. **Run** (dataset pinned + local to dodge the flaky on-run git clone; agent installs the
   wheelhouse offline, then `chimera solve`):
   ```bash
   git clone --depth 1 --branch dataset/terminal-bench-core/v0.1.x \
     https://github.com/laude-institute/terminal-bench /tmp/tbx     # local dataset
   export OPENROUTER_API_KEY=... CHIMERA_WHEELHOUSE_TAR=$PWD/wheelhouse.tar
   tb run -p /tmp/tbx/tasks -t fix-git \
     --agent-import-path chimera_installed_agent:ChimeraInstalledAgent \
     -m openrouter/deepseek/deepseek-chat-v3.1 --n-concurrent 1 --cleanup \
     --global-agent-timeout-sec 700
   ```

**Verified working:** the wheelhouse copies into the container and chimera installs **offline**
(`CHIMERA_INSTALL_OK`, chimera-agent-0.4.0 + litellm + …), `chimera solve` runs, Harbor grades, and
a real `results.json` is produced.

**Honest first data point (N=1, fix-git):** `is_resolved: false`, `failure_mode: agent_timeout` —
chimera's thorough multi-step loop on a cheap model exceeded the task timeout. Same lesson as the
local proxy and the VPS run: the scaffolding's cost dominates on a time-bounded benchmark with a
weak model. A non-timeout number needs a larger per-task timeout (hours of wall-clock + API for a
full subset) or a faster model — a deliberate, separate run, not a quick add.
