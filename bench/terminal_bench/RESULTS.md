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

## Results (2026-07-08, run complete)

**The prediction was WRONG in direction.** Chimera's scaffold scored *lower* than the bare baseline
on this run, not higher. Published as measured — no re-running to chase a different number (that
would be exactly the p-hacking this project's honest-benchmark discipline exists to prevent).

| arm | pass rate | 95% Wilson CI |
|---|---|---|
| baseline (bare, 1 attempt) | **7.5%** (3/40) | — |
| chimera (repo-map+ledger+checklist, 1 attempt) | **2.5%** (1/40) | — |

**Paired (McNemar/Wilson, via `chimera.eval.paired.compare_paired` — the project's own tool, no
special-casing):**

```
baseline               7.5%  (40 paired trials)
chimera                2.5%
paired delta (Δ)       -5.0%  95% CI [-5.0%, +1.6%]
discordant pairs       chimera +0 / baseline +2  (concordant carry no signal)
verdict                not significant (CI includes 0)
```

- **37/40 both fail**, **1/40 both pass** (`fix-permissions`).
- **2 discordant pairs, both favor baseline**: `fibonacci-server` (baseline passed, chimera got
  `test_timeout`), `hello-world` (baseline passed, chimera failed).
- **0 discordant pairs favor chimera.** The scaffold recovered nothing the baseline missed, on this
  40-task run.
- CI is wide and includes 0 — **not statistically significant** either direction, consistent with the
  pre-registered expectation of a tight/underpowered N=40 middle. What was NOT predicted is which side
  the point estimate landed on.

### The `hello-world` anomaly — investigated, not resolved

`hello-world` under the exact same model+flags graded **TRUE** in the Phase 1 probe (see PLAN.md) and
**FALSE** here. The chimera-arm solve log for this run shows the model claiming full, correct
completion ("✅ Created hello.txt ... ✅ Written 'Hello, world!' ... ✅ Verified... ✅ Confirmed no other
files") immediately followed by Chimera's own scaffold reporting **"failed after 1 attempt(s)"** — i.e.
Chimera's internal checklist/verifier disagreed with the model's own account, and Terminal-Bench's
independent grading agreed with the internal failure, not the model's claim. Two explanations are
consistent with the evidence and were NOT distinguished by this run:

1. **LLM run-to-run variance** (temperature > 0, no fixed seed) — a plausibly-different completion
   (e.g. a stray trailing space, extra file, or missing newline) on this attempt vs. the Phase 1 attempt.
2. **Concurrency artifact** — this run used `--n-concurrent 5` (Phase 1 probes ran serially/low
   concurrency); five simultaneous containers competing for host resources could shift timing enough to
   change a trivial task's outcome (e.g. a build step racing a write).

Not distinguished because doing so would require a repeated-seed, controlled-concurrency follow-up —
exactly the kind of "run it again to explain away the loss" that the honest-benchmark discipline says
not to do inline. Flagged here as an open question, not swept under the rug.

### Known measurement gaps (disclosed)

- **No token/cost telemetry from this adapter.** `ChimeraInstalledAgent.perform_task` always returns
  `total_input_tokens=0, total_output_tokens=0` — TB's own usage fields are not wired to the actual
  `chimera solve` process. No dollar-cost number is available from this run's results.json for either
  arm (future work: parse chimera's own budget/receipt output from the solve log).
- **N=40, single attempt, single model, single seed setting.** Both arms are near the floor for this
  competent-but-not-frontier model on a broad, hard, general task mix (many require compilers, ML
  runtimes, network access, or long multi-step infra work) — 37/40 both-fail leaves little
  discriminating middle. This is consistent with the pre-registered expectation of low absolute pass
  rates, just not with the predicted direction of the delta.
- **`--max-attempts 1` both arms**: Chimera's retry lever (its most-measured lift mechanism in
  `bench/local_lift`, where the goldilocks-model paired run showed +50pp) was deliberately held off
  here to isolate the scaffold's single-shot contribution and fit the native timeout cleanly. This run
  says nothing about Chimera's retry-loop lift — that already has a separate honest number
  (`bench/local_lift/RESULTS.md`).

### Honest verdict

On this pre-registered, single-attempt, N=40 Terminal-Bench-core slice: **Chimera's repo-map + ledger +
checklist scaffold did not outperform the bare model, and the point estimate favored the baseline (not
significant).** This does not contradict the project's separate, positive finding that Chimera's
**multi-attempt retry loop** lifts a goldilocks-weak model (`bench/local_lift`) — it tests a different
mechanism (single-shot scaffolding context/structure vs. iterative retry-with-verification) on a
different, harder, more heterogeneous task mix. Both numbers are published because both are true.

**Next, if pursued:** (a) a controlled-concurrency, fixed-temperature repeat to settle the `hello-world`
anomaly; (b) a multi-attempt (`--max-attempts 3`) A/B on this same Terminal-Bench slice, budget
permitting, to test the mechanism that `bench/local_lift` already shows lift for, on the harder/broader
official benchmark.

---

# Follow-up A — anomaly repeat (controlled concurrency, 2026-07-08)

**Purpose:** distinguish the two explanations for the `hello-world` pass→fail flip: LLM run-to-run
variance vs. a `--n-concurrent 5` resource-contention artifact.

**Note on "fixed seed":** not truly achievable through this benchmark's install path — the container
installs `chimera-agent` from PyPI, which runs at temperature 0.7 with no `seed` param forwarded to
deepseek/OpenRouter. So instead of a single (non-deterministic anyway) temp-0 run, this measures the
**intrinsic variance directly**: each task run **5× at `--n-concurrent 1`** (serial, zero contention),
chimera arm (full scaffold, max-attempts 1).

**Pre-declared reading (before running):**
- If `hello-world` passes ~5/5 serial → the Phase-2 single failure is best explained by **concurrency
  contention** (the −5pp is partly artifact of `--n-concurrent 5`).
- If it passes only ~1–3/5 serial → it's **intrinsic temp-0.7 variance** (the scaffold's checklist
  verifier sometimes false-fails a correct trivial solve), and the Phase-2 sample caught a genuine miss.

_(results below)_

---

# Follow-up B — the `--max-attempts 3` A/B (mechanism test) — PRE-REGISTERED

**Different mechanism from Phase 2.** Phase 2 held `--max-attempts 1` both arms to isolate the
single-shot scaffold. This tests Chimera's **retry-with-verification loop** — the mechanism
`bench/local_lift` already shows lift for (+50pp on a goldilocks-weak model). Same 40-task slice.

### Arms

| arm | flags | budget |
|---|---|---|
| **baseline** | `--max-attempts 1 --no-remember --no-collect --no-evolve-skills` (raw 1-shot) | — |
| **chimera** | `--repo-map --progress-ledger --checklist --max-attempts 3 --no-remember --no-collect --no-evolve-skills` | up to 3 verify-or-revert attempts |

- **Disclosed budget override (NON-leaderboard):** `--global-agent-timeout-sec 600` (CHIMERA_SOLVE_TIMEOUT
  550) so the loop has room to actually run 3 attempts (~150–180s each). This is a *mechanism test*, not
  a leaderboard number — stated explicitly. Both arms get the same 600s budget (baseline 1-shot won't use
  it). Concurrency `--n-concurrent 2` (controlled, well below the Phase-2 5).

### Prediction (registered 2026-07-08, before running)

deepseek is competent (not goldilocks-weak), and on this hard/heterogeneous mix many failures are
environmental (missing tools/runtimes, wrong high-level approach) that retry doesn't fix. But retry
*should* recover a few tasks where attempt 1 was close.

- **Direction:** chimera (3-attempt loop) **≥** baseline (1-shot) — the retry loop should not hurt and
  may recover 1–3 both-fail tasks.
- **Effect size:** small-to-moderate, **Δ ≈ +2 to +12pp**.
- **Significance:** likely still **not significant** at N=40 (floor effect: most hard tasks stay failed
  regardless of attempts).
- **Contrast with Phase 2:** I explicitly expect this to land on the *opposite* side of the Phase-2
  point estimate (which favored baseline) — because retry is the mechanism with prior positive evidence,
  unlike single-shot scaffold. If it does NOT (chimera ≤ baseline again), that's a strong honest signal
  that Chimera's loop doesn't transfer to this benchmark regime, and it stands.

_(results below)_
