# Terminal-Bench A/B — Chimera scaffold vs raw model (same model)

**Honest-benchmark discipline:** the prediction below is registered BEFORE the run. The result is
published regardless of outcome (win, loss, or null). No re-running to chase significance.

## Setup (locked, Phase 1)

- **Harness:** official `terminal-bench` (tb) on WSL Ubuntu + Docker Desktop, dataset
  `terminal-bench-core==0.1.1`.
- **Model (both arms):** `openrouter/deepseek/deepseek-chat-v3.1`.
- **Timeout:** native per-task `max_agent_timeout_sec` (360s for this subset), no `--global-agent`
  override. `CHIMERA_SOLVE_TIMEOUT=300`. Leaderboard-honest.
- **Install / workdir:** network-first PyPI + bootstrap chain + wheelhouse fallback; solve runs in
  `/app` (see `chimera_installed_agent.py`).
- **Concurrency:** `--n-concurrent 4`.

### Arms (differ only in Chimera's scaffolding; single attempt both)

| arm | flags | what it is |
|---|---|---|
| **baseline** | `--max-attempts 1 --no-remember --no-collect --no-evolve-skills` | the bare agent loop: model + native tools, one attempt, no Chimera scaffolding |
| **chimera** | `--repo-map --progress-ledger --checklist --max-attempts 1 --no-remember --no-collect --no-evolve-skills` | + repo-map context, Magentic-One progress-ledger, requirement-checklist |

Both arms are Chimera-installed and use the same model, timeout, workdir, and install path — the ONLY
difference is the three scaffolding flags. This isolates *Chimera's scaffolding contribution* on a
standard benchmark. (The loop's retry lever — `--max-attempts >1` — is held at 1 both arms to fit the
360s budget cleanly with no timeout confound; a multi-attempt test is future work needing a larger
budget.)

### Subset (N=40, pre-declared, deterministic)

The first 40 task-ids alphabetically among the 56 tasks with `max_agent_timeout_sec == 360` (the
uniform-budget slice; excludes the 24 longer/heavier tasks incl. the biggest swe-bench builds —
disclosed, not cherry-picked for difficulty):

```
build-initramfs-qemu build-tcc-qemu cartpole-rl-training chess-best-move configure-git-webserver
crack-7z-hash crack-7z-hash.easy crack-7z-hash.hard create-bucket cron-broken-network csv-to-parquet
decommissioning-service-with-sensitive-data download-youtube extract-moves-from-video fibonacci-server
fix-git fix-pandas-version fix-permissions get-bitcoin-nodes git-workflow-hack gpt2-codegolf hello-world
heterogeneous-dates hf-model-inference intrusion-detection jupyter-notebook-server modernize-fortran-build
new-encrypt-command nginx-request-logging oom openssl-selfsigned-cert organization-json-generator
password-recovery path-tracing path-tracing-reverse polyglot-c-py polyglot-rust-c processing-pipeline
prove-plus-comm pytorch-model-cli
```

## Prediction (registered 2026-07-08, before running)

deepseek-chat-v3.1 is a **competent** model (not the goldilocks-weak regime where scaffolding lifts
most, per `bench/local_lift`). On this mix of many hard/infra tasks, single attempt both arms:

- **Absolute pass rates: low**, roughly **10–30%** each arm (many tasks need compilers, ML, network,
  or long multi-step work that a competent model still often misses in one attempt).
- **Direction:** chimera ≥ baseline. The scaffold (repo-map + ledger + checklist) should help on the
  multi-step tasks, hurt on none.
- **Effect size:** small, **Δ ≈ 0 to +8pp**.
- **Significance:** **probably NOT significant** at N=40 — few discordant pairs expected; the paired
  McNemar/Wilson CI likely includes 0. Ceiling (both pass easy) + floor (both fail hardest) shrink the
  discriminating middle.

If the result contradicts this (e.g. a large or negative Δ), that is the finding and it stands.

## Results

_(filled in after both arms complete — see below)_
