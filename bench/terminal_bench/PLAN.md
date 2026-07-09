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

## Phase 1 — DONE (2026-07-08): config locked, two integration bugs fixed on real greens

The bet in the original table (that the deepseek full scaffold would time out) was **wrong** — the
timeout was never the real blocker. Measured on real tasks (native per-task timeouts, no override):

- **Timeout FITS natively.** deepseek + the lean scaffold graded fix-git in ~2-3 min, well under the
  360s that 56/80 tasks allow. No `--global-agent-timeout` needed for the common case.
- **Bug 1 — workdir (fixed).** The solve now `cd /app` first. TB's client container works in /app and
  tests assert absolute `/app/...` paths (hello-world → `/app/hello.txt`); exec_run defaulted to the
  image WORKDIR (`/`), so files landed where the grader never looked → false. **Fix → hello-world
  is_resolved TRUE** (first real chimera pass on Terminal-Bench).
- **Bug 2 — install portability (fixed).** Base images are heterogeneous: some ship pip, some a bare
  `/usr/bin/python3` with no pip/ensurepip, some are PEP-668 externally-managed, some lack curl AND
  wget. Install is now a bootstrap chain: **network PyPI first** (`chimera-agent` is public → resolves
  the container's own ABI) via `python3 -m pip --break-system-packages`, bootstrapping pip through
  ensurepip / a **urllib-fetched get-pip.py** when absent, then the offline cp313 wheelhouse as last
  resort. **Fix → fix-permissions is_resolved TRUE** (was `agent_installation_failed`); csv-to-parquet
  now installs+runs (false/unset = honest signal, no longer an install artifact).

**Config locked for Phase 2:**
| knob | value |
|---|---|
| model | `openrouter/deepseek/deepseek-chat-v3.1` |
| timeout | native per-task (`CHIMERA_SOLVE_TIMEOUT=300`, no global override) |
| flags | `--repo-map --progress-ledger --checklist --max-attempts 1 --no-remember --no-collect --no-evolve-skills` |
| install | network-first + bootstrap chain + wheelhouse fallback |
| workdir | `/app` |

Phase-1 sample verdicts (honest mix): hello-world ✅, fix-permissions ✅, fix-git ✗, csv-to-parquet ✗.
Cost so far ≈ $1-2 (6 small probe runs). A per-container install-fail log (`chimera_install_fail.log`)
and a solve log (`chimera_solve.log`) are now dropped into each task's run dir for diagnosis.

## Phase 2 — DONE (2026-07-08): the A/B number, published

N=40, native per-task timeout, deepseek-chat-v3.1 both arms, pre-registered prediction (`a2535a4`),
result computed with the project's own `chimera.eval.paired.compare_paired`. **Full write-up in
`RESULTS.md`.** Headline: baseline 7.5% (3/40) vs chimera 2.5% (1/40), Δ −5.0pp, CI [−5.0%, +1.6%],
**not significant** — the prediction's direction was wrong (chimera did not beat baseline on this
single-attempt, N=40, broad-task slice). Published as measured, no re-run to chase significance. See
RESULTS.md for the `hello-world` anomaly investigation and disclosed measurement gaps (no token/cost
telemetry from this adapter; `--max-attempts 1` both arms means this doesn't test Chimera's retry-loop
lift mechanism, which has a separate positive number in `bench/local_lift`).

## Follow-ups — DONE (2026-07-09), both in `RESULTS.md`

- **A. Anomaly repeat (controlled concurrency).** `hello-world` 5× serial (`--n-concurrent 1`): 2/5 —
  ~40% reliable even at zero contention → the Phase-2 flip was **intrinsic variance, not a concurrency
  artifact**; the scaffold's checklist can false-fail a correct trivial solve. `fix-permissions` 5/5,
  `fibonacci-server` 0/5.
- **B. `--max-attempts 3` A/B (retry-loop mechanism).** Same 40 slice, disclosed budget override
  `--global-agent-timeout-sec 600`, `--n-concurrent 2`. baseline-1shot 2.5% (1/40) vs chimera-3attempt
  5.0% (2/40), Δ +2.5pp, CI [−1.5%, +2.5%], **not significant** — direction matched the pre-registered
  prediction (opposite side of Phase 2), the loop **recovered `oom` and lost nothing**, but at 1-vs-2
  passes it's the noise floor. Baseline itself moved 7.5%→2.5% between runs → **the floor is
  variance-dominated**; neither delta is separable from noise at N=40. The signal-bearing regime for
  Chimera's lift stays the goldilocks paired run in `bench/local_lift` (17%→67%). A significant
  Terminal-Bench number needs larger N, a weaker model, or an easier discriminating slice.

## Environment note (resolved 2026-07-08)

Docker Desktop was crash-looping (v4.66): each service failed to remove a stale AF_UNIX socket
("Não é possível o acesso ao arquivo … A sintaxe do nome do arquivo … está incorreta") — first the
Inference/Model-Runner (`dockerInference`), then the Secrets Engine (`docker-secrets-engine/engine.sock`).
Fix: remove the orphaned sockets **via WSL** (Windows tools can't delete dangling AF_UNIX sockets) —
`wsl bash -c 'find /mnt/c/Users/<u>/AppData/Local/Docker /mnt/c/Users/<u>/AppData/Local/docker-secrets-engine -type s -delete'` —
and set `"EnableDockerAI": false` in `%APPDATA%\Docker\settings-store.json` (the Model Runner is the
crash source and we don't use it). After that Docker Desktop + WSL integration came up clean.

If it recurs after a bad shutdown: kill Docker processes, delete the orphaned sockets via WSL, relaunch.
