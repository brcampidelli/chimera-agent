# Decision latency under a queue — pre-registration

Written 2026-10-07, **before any measured call** (the smoke in `RUN.md` checks the runner and is not
read). Local only (Ollama `qwen3:4b` through the shipped `LocalLogprobBackend`), US$ 0.

## Question

arXiv 2609.23136 compares decision models with LLMs under a **1 s budget**. Ours has none until the
`deadline_s` option (`chimera/decisions/contract.py`, off by default), and nothing in this repository
says what a deadline would cost: the docstrings quote **0.75 s** per call on the RTX 5070
(`local.py`) and **0.3 s** (`band.py`), both measured with **one** caller. The band, the `decide`
tool and verified answers can ask at the same time — a parallel batch, several conversations, the
bot — and Ollama serves them from one model on one GPU.

> For the shipped local decider, what are p50 / p95 / p99 decision latency and the fraction of calls
> back within 0.5 s and 1 s (ϕ₀.₅, ϕ₁) with 1, 2, 4 and 8 concurrent clients?

The answer is what a deployment reads before it sets `CHIMERA_GOVERNANCE_BAND_DEADLINE_S`: a deadline
the decider misses is a REVIEW card, so ϕ at that deadline is the share of band-consulted actions that
would NOT become a card because of latency alone.

## The instrument (fixed now)

* **Items:** the 55 governance items (`bench/governance_judge` easy + ambiguous; 24 attacks, 31
  benign twins) — the corpus the shipped map was fitted on (`bench/jev_decisions`), rendered by
  `render_action("run_shell", …)` as production renders them.
* **Call:** the product's own path, `Decider(LocalLogprobBackend("http://127.0.0.1:11434",
  "qwen3:4b"), CalibrationMaps.shipped()).decide("governance.danger", state, DANGER)`, with **no
  cache** (a cached reading costs nothing and would measure the cache) and **no deadline** (the tail
  is what is being measured; a deadline would censor it). One backend — one HTTP client — per
  concurrent client, as separate surfaces have.
* **Latency:** `Answer.seconds` (perf_counter around the backend call, the time a caller waits), plus
  the wall time of each level for throughput.
* **Server:** whatever `OLLAMA_NUM_PARALLEL` / `OLLAMA_MAX_QUEUE` the server runs with, recorded as
  the runner can see them (the runner's environment; the server's own is not queryable — stated as
  "server default" when unset). The Ollama version and the build from `/api/show` are recorded.
* **Levels and order:** concurrency c ∈ {1, 2, 4, 8}. Two sweeps: **A ascending** (1, 2, 4, 8) and
  **B descending** (8, 4, 2, 1). In each (sweep, level) the 55 items are asked **twice**, each pass in
  an order shuffled with a registered seed (`seed = 1000·sweep_index + 10·c + pass`), from one shared
  queue that the c clients drain. 110 calls per (sweep, level), **880 measured calls** in all, plus
  **3 warm-up calls** before each sweep that are recorded and not read.
* **Unload:** the runner unloads the model at the end (`keep_alive: 0`) **only if it was not loaded
  when the run started**, so it neither leaves the GPU loaded nor pulls a model out from under
  another job.

## Readings

1. **Control (read first; nothing else is read if it fails).** (a) Every measured answer names the
   build `qwen3:4b@Q4_K_M`, is calibrated, and has no halt. (b) The GPU was idle at the start of each
   sweep: no compute process other than Ollama in `nvidia-smi` and utilisation < 10% — the runner
   refuses to start otherwise (`--allow-busy-gpu` exists for the smoke only, and marks every row).
   (c) Drift: the c = 1 p50 of sweep A and sweep B within **20%** of each other. A failure is
   reported as the instrument moving (another load, thermal throttling — the laptop GPU runs at
   ~84 °C under load), not read.
2. **Per (sweep, level):** p50, p95, p99 (nearest rank), ϕ₀.₅, ϕ₁, mean, max, throughput (calls per
   wall second), halts.
3. **Verdict stability:** the share of items whose verdict at the band's 0.50 differs between c = 1
   and c = 8 (the same calls at temperature 0; concurrency changes the batch the server forms, and
   §2t says batch size can change greedy output). Reported, not part of the decision.

## Predictions (written so they can be wrong)

| c | p50 | ϕ₁ |
|---|---|---|
| 1 | ≤ 0.75 s (the quoted floor) | ≥ 0.95 |
| 2 | ≤ 1.5 s | between 0.4 and 0.95 |
| 4 | > 1 s | ≤ 0.5 |
| 8 | > 2 s | ≤ 0.2 |

The shape predicted is **queueing**: if Ollama serves one decision at a time, latency at c clients is
about c × the service time and throughput is flat in c; if it batches (`OLLAMA_NUM_PARALLEL` > 1),
throughput rises with c and p50 grows slower than c. The run tells which.

## Decision rule (fixed now)

Nothing ships as a default from this bench: the deadline stays **OFF**. What the run decides is the
sentence the settings and `.env.example` may print next to `CHIMERA_GOVERNANCE_BAND_DEADLINE_S`:

* **Supported concurrency at a deadline d ∈ {0.5 s, 1 s}:** the largest c with ϕ_d ≥ 0.95 in **both**
  sweeps. If c = 1 itself misses it at d = 1 s in either sweep, the text says "a 1 s deadline turns
  more than 5% of band-consulted actions into cards even with one caller on this hardware".
* The **shape** (queueing or batching) is stated only if both sweeps agree on it: throughput at c = 8
  within 25% of c = 1 in both is "serialised"; ≥ 1.5× in both is "batched"; anything else is "not
  resolved".

## §11 — the interval

ϕ is a proportion over 110 calls per cell, read with **Wilson** (`chimera.eval.proportions.wilson`).
The calls in a cell are not independent (they share a queue), so the Wilson interval is nominal and
printed as such; the decision rule leans on the two sweeps agreeing (§15), not on the interval.
Quantiles get a distribution-free order-statistic interval (binomial, 95%). No bootstrap.

## §12 — the margin

No equivalence or non-inferiority claim. "Flat throughput" in the shape rule is a descriptive label
with its 25% band fixed above, not a claim of no difference.

## §13 — the controls

* **Trivial-agent / random arm:** not applicable — nothing is scored for correctness and nothing is
  selected; latency has no "do-nothing" floor to compare against.
* **Grader hijack / argument-level attack scoring / detection probe / placebo arms / rule-withdrawn
  arm:** not applicable — no grader, no attack outcome, no judge score, nothing added to a prompt.
* **What does apply:** the instrument control in Reading 1 (build, calibration, no halt — the call is
  the product's call), the idle-GPU check, and the A/B drift check.

## §14 — the model scope

`qwen3:4b` Q4_K_M on one RTX 5070 Laptop GPU through Ollama, as shipped. The numbers are this
model on this machine; a different GPU, quantisation or Ollama version is a different instrument and
is re-measured, not extrapolated (lessons §2ae). No component is removed or defaulted off on this
evidence.

## §15 — the perturbations

The contrasts read are between concurrency levels (and the shape). Each is read under the registered
perturbations: **sweep order** (A ascending, B descending) and the **pass order** of items (two seeded
shuffles per cell), and at **both** deadlines (0.5 s and 1 s). A level counts as supported only if
ϕ_d ≥ 0.95 holds in both sweeps; a shape only if both sweeps give it. Otherwise the report prints
"not robust" with each reading.

## Amendment 1 — 2026-10-07, after the smoke, before any measured run

The smoke (16 calls, `--allow-busy-gpu`, while another process held the GPU at ~94%) timed out
**12 of 14** calls at the backend's 30 s (`DEFAULT_TIMEOUT_S`). The registration was silent on what a
timeout is, and the obvious reading — drop the halt — reports the latency of the calls fast enough to
answer. So, fixed before the run:

* A call that halts on a **timeout** (`ReadTimeout`, `WriteTimeout`, `PoolTimeout`, `ConnectTimeout`)
  is a **censored latency** of at least 30 s. It stays in its cell: a miss at both deadlines, and its
  30 s enters the quantiles (which are then lower bounds; the cell prints its `timeouts`).
* Control 1(a) reads: every measured answer that did **not** time out names the build, is calibrated
  and has no halt. A halt that is not a timeout (connection refused, model not found) still fails the
  control.
* Verdict stability (Reading 3) compares only calls that answered.

Nothing else changes; the smoke rows are not read and are not committed.

## Amendment 2 — 2026-10-07, after an aborted run, before the next measurement

The first full run stopped before sweep B: the guard of control 1(b) read **54% utilisation at
86 °C with no other compute process** — the load of sweep A's own last cell (c = 8, 110 calls in
36.6 s) still draining. A harness defect: the guard could not tell our own tail from someone else's
job. Fixed before the next measurement:

* Before each sweep, **another compute process** (or no `nvidia-smi`) still refuses at once.
* With no other process, the guard **polls every 5 s for up to 5 min** until utilisation is under
  10%, and refuses only if the budget runs out. Each `gpu` row records `waited_s`, `polls`, and the
  temperature at the first and last reading; the report prints them under the control.
* The threshold (10%), the rest of control 1 and every reading and decision rule are unchanged.

**The aborted run is discarded and the run restarts from zero.** Sweep A's 440 rows were measured
under the same configuration and are probably valid, but reusing them would make A and B straddle a
harness change and a restart (model load, a different thermal start), and the A/B comparison is
exactly the §15 perturbation the decision leans on. The aborted file is kept outside the
repository, not read and not committed.
