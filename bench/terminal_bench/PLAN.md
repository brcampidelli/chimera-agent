# Terminal-Bench — plan to close an honest leaderboard number

Goal: publish a **cost × performance** number on a standard benchmark, with a CI, as an
**A/B (baseline raw model vs Chimera loop, same model)** — matching the honest-benchmark
discipline (register the prediction before running; publish even if Chimera loses).

## Phase 0 — root-caused (2026-07-08)

The earlier `agent_timeout` is **not** a tmux end-detection bug. `ChimeraInstalledAgent.perform_task`
already bypasses the fragile tmux completion signal by driving install + solve through the
container's **synchronous** `container.exec_run`. The timeout is the harness's per-task
`max_agent_timeout_sec` (each task's yaml), enforced by `asyncio.wait_for` around `perform_task`
(`terminal_bench/harness/harness.py::_run_agent_with_timeout`, ~L633-673). When the solve runs
longer than that limit, TB records `FailureMode.AGENT_TIMEOUT` regardless of our own `timeout 600`.

Consequence: the grading path is correct; what's needed is for the solve to **finish within the
allowed budget**. Two honest routes below.

### Validation command (prove grading works end-to-end)

Run one easy task with a generous global agent timeout so the solve finishes and TB runs the task
tests (a real is_resolved verdict instead of agent_timeout):

```bash
# in WSL Ubuntu, Docker Desktop running with WSL integration ON:
cd ~/tbench
.venv/bin/tb run \
  --agent-import-path chimera_installed_agent:ChimeraInstalledAgent \
  --dataset-path /tmp/tbx/tasks --task-id hello-world \
  --global-agent-timeout 1800 \
  --output-path runs_val
# expect: a graded trial (is_resolved true/false from the tests), NOT agent_timeout.
```

## Phase 1 — pick the config that FITS the per-task timeout (~$2, 2-3 tasks)

Measure wall-clock/task per config; pick one that finishes within the task's own
`max_agent_timeout_sec` while still scoring. `CHIMERA_TB_FLAGS` / `CHIMERA_TB_MODEL` env control it.

| config | env | bet |
|---|---|---|
| deepseek, full scaffold | `CHIMERA_TB_FLAGS="--repo-map --progress-ledger --checklist --max-attempts 1 ..."` | likely times out (latency ~14s/call) |
| deepseek, bench-lite | drop `--progress-ledger`/`--checklist`, `--max-attempts 1` | fewer calls -> fits |
| fast model | `CHIMERA_TB_MODEL=openrouter/google/gemini-2.x-flash` (low latency) | full loop fits |

## Phase 2 — the A/B number (~$10-15, N≈30-50 Terminal-Bench-Core tasks)

Two arms, same model, on a fixed subset:
- **baseline**: raw model, 1-shot (minimal agent).
- **chimera**: the Phase-1 config.

Report pass rate + tokens/cost per arm; verdict via `chimera bench-compare` (Wilson/Newcombe CI).
Register the prediction in `bench/terminal_bench/RESULTS.md` BEFORE running. Respect each task's
`max_agent_timeout_sec` (leaderboard-honest); if a `--global-agent-timeout` override is used, state
it explicitly in RESULTS.

Cost: deepseek ~$0.05-0.20/task chimera + ~$0.01 baseline -> N=40×2 ≈ $7-10, + Phase-1 ~$2 ≈ **$10-15**.
Where: **WSL Ubuntu + Docker Desktop** (local; `~/tbench` already bootstrapped). Not the prod VPS.

## Current blocker

Docker Desktop must be **running with WSL integration enabled for the Ubuntu distro**
(Settings → Resources → WSL Integration). As of this write it was stopped; starting it headlessly
did not bring the engine up (likely an interactive Docker Desktop window). Once `wsl -e bash -lc
"docker ps"` works, the validation command above runs in minutes at ~$0.
