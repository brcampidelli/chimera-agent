# Terminal-Bench — plan to close an honest leaderboard number

Goal: publish a **cost × performance** number on a standard benchmark, with a CI, as an
**A/B (baseline raw model vs Chimera loop, same model)** — matching the honest-benchmark
discipline (register the prediction before running; publish even if Chimera loses).

## Phase 0 — DONE (2026-07-08): the harness grades our agent

Validated end-to-end on WSL Ubuntu + Docker Desktop (after unblocking a Docker Desktop
crash — orphaned AF_UNIX sockets + the Inference/Model-Runner service; fixed by removing
the sockets via WSL and setting `EnableDockerAI:false` in Docker's settings-store.json):

- **oracle** on `fix-git`: **100% resolved** in 21s — harness + dataset + container + grading all work.
- **chimera agent** on `fix-git` (deepseek, `--global-agent-timeout-sec 1100`): graded in **119s**,
  `is_resolved: false`, **`failure_mode: "unset"`** — i.e. NO agent_timeout. The agent installs
  (wheelhouse, offline), runs `chimera solve` via `container.exec_run`, finishes, and TB grades with
  the task's own tests. `is_resolved: false` is the honest benchmark signal (deepseek didn't fix the
  git conflict in that lean single-attempt), not a harness bug. **The blocker is gone.**

## Phase 0 — root-cause reference

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
  --global-agent-timeout-sec 1800 \
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

## Environment note (resolved 2026-07-08)

Docker Desktop was crash-looping (v4.66): each service failed to remove a stale AF_UNIX socket
("Não é possível o acesso ao arquivo … A sintaxe do nome do arquivo … está incorreta") — first the
Inference/Model-Runner (`dockerInference`), then the Secrets Engine (`docker-secrets-engine/engine.sock`).
Fix: remove the orphaned sockets **via WSL** (Windows tools can't delete dangling AF_UNIX sockets) —
`wsl bash -c 'find /mnt/c/Users/<u>/AppData/Local/Docker /mnt/c/Users/<u>/AppData/Local/docker-secrets-engine -type s -delete'` —
and set `"EnableDockerAI": false` in `%APPDATA%\Docker\settings-store.json` (the Model Runner is the
crash source and we don't use it). After that Docker Desktop + WSL integration came up clean.

If it recurs after a bad shutdown: kill Docker processes, delete the orphaned sockets via WSL, relaunch.
