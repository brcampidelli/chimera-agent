# Pre-registration — does provider-side prefix caching change an agent's trajectory on our hosted route?

**Registered 2026-09-15, before any solve was run.** Study 19, item A1. Source:
arXiv:2609.04748 ("Same Request, Different Answer"): with the prefix cache **off**, identical agentic
requests reproduced bit-identically in **800/800** episodes; with it **on**, the trajectory changed in
**36.2%** of episodes at 16-bit and **75.0%** at 4-bit. Measured on self-hosted vLLM/SGLang.

## Why it is our problem

- `chimera/providers/prompt_cache.py` engages caching deliberately (automatic on DeepSeek routes; an
  explicit breakpoint on Anthropic), and the byte-identical `WORKER_SYSTEM` prefix is shared across
  every worker.
- **No stored artefact records cache state.** Checked 2026-09-15: `hb-driver.jsonl` (556 rows) has no
  cache or provider field; the factorial's 557 result JSONs mention neither `cached_tokens` nor
  `provider`; `.chimera/runs.jsonl` carries neither. `StepLog.cache_hit_rate` exists in memory and was
  never written to disk. So whether any published paired number was produced under cache hits is
  **unknowable from what we kept** — which is the first result of this item, and it is free.
- Every paired comparison in `bench/` — the factorial's within-cell SD 0.073, the seed floor of §2m,
  `fusion_paired`, `edit_tools` — was run with the cache engaged and unrecorded.

## What the paper's dichotomy needs, and what we can do on a hosted API

The paper's "cache off" is a server flag. OpenRouter/DeepSeek caching is automatic and has no
per-request off switch. The only lever available to us is to **defeat** the cache by making the
prefix unique per run: a nonce line prepended to the system prompt (`CHIMERA_PREFIX_NONCE`). That is
the honest analogue, and it comes with a verification the paper did not need: the provider reports
`cache_read_tokens`, so each condition can prove it did what it claims (§2r — an intervention
reports how much it acted, and ~0 is a defect, not a result).

## Temperature — the design decision, stated

The factorial ran at the agent default **T = 0.2** (`AgentConfig.temperature`). At T > 0, replicas
diverge by sampling regardless of the cache, and a cache effect is an increment on top of that,
hard to power with k in the single digits. At **T = 0**, any logit perturbation that flips an
argmax shows in the trajectory, and the paper's 800/800 baseline becomes reproducible. So:

**This run measures the mechanism at T = 0.** It is the sensitive instrument. If the cache adds no
divergence at T = 0, it cannot add a detectable amount at T = 0.2 either, and the archive stands. If
it does add divergence at T = 0, a second, pre-registered run at T = 0.2 decides how much of our
published noise it explains. That second run is **not** part of this registration.

## Design

- **Model:** `openrouter/deepseek/deepseek-v3.2`, the factorial's. **Route pinned** to one provider
  (`provider: {order: [...], allow_fallbacks: false}`) for every solve, and the provider echoed in the
  receipt. A run whose provider differs from the pinned one is discarded and counted as such.
- **Tasks (4):** factorial tasks whose published outcome is neither saturated nor floored, so a flip
  is *possible*: `042-api-schema-migration` (mean 0.40), `086-sql-migration-preflight-rollback`
  (0.64), `087-cli-parser-bug-tests` (19/24 pass after the pytest fix), `092-schema-drift-audit`
  (0.34). Chosen before any solve; not to be swapped.
- **Conditions (2):** `SHARED` — nonce unset, identical prefix for every replica (the production
  configuration); `NONCE` — a fresh UUID per solve prepended to the system prompt.
- **Replicas:** k = 4 per (task, condition) → **32 solves**, run **sequentially, interleaved**
  (S, N, S, N, …) so time-of-day and provider load are balanced across conditions.
- **Everything else** as `hb_solve.sh`: `--max-attempts 1 --max-steps 120 --max-usd 2.0`,
  `CHIMERA_HOST_EXEC=allow`, the same flags, the same grader.
- **Recorded per solve:** ordered `tool_names`, final diff fingerprint (sha256 of the unified diff),
  `outcome_score`, per-step `cached_tokens` and `prompt_tokens` (→ cache hit rate), `provider`,
  `usd`.

## Instrument check — before spending on the grid

Two solves of one task, one per condition. Stop if either fails:

1. `cached_tokens` is **reported** (not `None`) by this route. A silent provider makes the contrast
   unverifiable, and the study halts here with that recorded.
2. The two conditions **differ** in cache hit rate in the expected direction on the second solve of
   the same task (SHARED > NONCE). If they do not, the nonce is not defeating the cache (the prefix
   may be serialised after something else) and the instrument is fixed before any more money.

## Metrics

- **M1 — cache hit rate** per solve, by condition. The intervention check.
- **M2 — trajectory identity:** for each (task, condition) cell, whether the 4 replicas have the
  same ordered `tool_names` **and** the same diff fingerprint. Primary.
- **M3 — outcome flip rate:** within-cell SD of `outcome_score`. Secondary.
- **M4 — provider identity:** every receipt names the pinned provider. A gate, not a metric.

## Predictions

- **P1.** Under NONCE at T = 0, ≥ 3 of 4 cells are trajectory-identical (the paper says 4/4; a hosted
  route may add a little batching nondeterminism).
- **P2.** If the effect crosses the API: SHARED is identical in ≥ 2 fewer cells than NONCE.
- **P3.** M1: NONCE hit rate ≈ 0 on every solve; SHARED hit rate > 0 from the second replica of each
  task on.

## Decision rule — written before the numbers

| NONCE identical cells | SHARED identical cells | reading |
|---|---|---|
| ≤ 2 of 4 | any | hosted nondeterminism beyond the cache; the paper's dichotomy does not transfer to this route; the T = 0 replicate floor is recorded and the cache question is **closed as not isolable here** |
| ≥ 3 of 4 | ≥ NONCE − 1 | the cache adds nothing detectable at T = 0 on this route — **closed; the archive stands** |
| ≥ 3 of 4 | ≤ NONCE − 2 | the effect crosses — **reopen**: a registered T = 0.2 follow-up, and every published paired `RESULTS.md` gets a note that cache state was unrecorded |

Whatever the reading, two things ship regardless: `cache_read_tokens` and `provider` on every
attempt receipt from now on, and the nonce switch, off by default, documented as a measurement
instrument.

## Budget

Factorial cost per solve ≈ US$ 0.053 (US$ 29.28 / 552). 32 + 2 solves ≈ **US$ 1.8**. Ceiling
**US$ 5**; the driver stops at the ceiling.

## What this cannot show

- Anything about T = 0.2 directly (see above).
- Anything about Anthropic routes: the breakpoint mechanism is different and the earlier probe on
  `claude-haiku-4.5` surfaced no cache tokens for reasons never established (`prompt_cache.py`).
- Whether the *provider's* cache is the only cache: OpenRouter may sit in front of DeepSeek's own.
  `cache_read_tokens` is whatever the route reports; if it reports on one layer only, M1 is a lower
  bound on engagement, not the whole of it.

---

## Amendment 1 — 2026-09-15, after the instrument check and before any further solve

The instrument check ran three solves of `042-api-schema-migration` (SHARED, SHARED, NONCE), all
served by the route **`AtlasCloud`** (not DeepSeek's own — the model id is served by several
providers, and the factorial's 552 solves were served by one nobody recorded):

| solve | `cache_read_tokens` | `prompt_tokens` | hit rate | fingerprint |
|---|---:|---:|---:|---|
| SHARED r1 | 756,096 | 897,645 | 0.842 | `b3e3fd19…` |
| SHARED r2 | 517,504 | 599,853 | 0.863 | `7c78ad5e…` |
| NONCE r1 | 360,192 | 433,685 | 0.831 | `9c89b455…` |

**Condition 1 passed** — the route reports cache usage. **Condition 2 failed** — the nonce did not
reduce the hit rate (0.831 against 0.842/0.863). The reason is structural, not a bug in the switch: a
multi-step agent re-sends its own transcript at every step, and the provider serves that *intra-run*
prefix from cache whatever the system prompt says. The nonce only prevents sharing the system prompt
*across* runs, a sliver of the tokens. The paper's "cache off" is a server flag with byte-identical
inputs; **on a hosted route there is no such lever** — anything that defeats the cache changes the
bytes, and byte-identical replicas are by definition cacheable. The registered condition cannot be
implemented here, and the registration says to stop before the grid. The grid is not run.

**What replaces it.** The one cell the route *can* exhibit is SHARED at T = 0: byte-identical
replicas with the cache on — the paper's "cache on" condition, on our route. Two replicas already
diverged (fingerprints differ; both scored 0.74; the tool sequences part at the sixth call). Two
alert, three decide (§2x): **two more SHARED replicas of the same task, pinned to `AtlasCloud`**,
k = 4 in all. This measures the T = 0 replicate floor with the cache on. It **cannot attribute** that
floor to the cache rather than to any other hosted nondeterminism (batching, kernels, routing inside
the provider), and the result will say so.

Reading rule for the amended cell: 4/4 identical → T = 0 reproduces on this route, and the paper's
36.2% has no visible counterpart here. Fewer → byte-identical T = 0 runs diverge on this route; the
paper's dichotomy is not testable here, and every replicate floor we have published includes hosted
nondeterminism of unknown composition. Either way the cache question closes as **not isolable on a
hosted route**, which is decision-table row 1 reached through the instrument rather than through M2.

Spend so far US$ 0.28; the two extra solves ≈ US$ 0.20. Ceiling unchanged.

---

## Amendment 2 — 2026-09-15, after the four SHARED replicas and before any further solve

The amended cell ran: four SHARED replicas of `042`, all served by `AtlasCloud`, all four with
distinct trajectories (fingerprints `b3e3fd19`, `7c78ad5e`, `b193f1ae`, `c72960ac`; scores 0.74,
0.74, 0.40, 0.74). Read from the receipts alone that would be "byte-identical T=0 runs diverge on
this route". **It is not, and the artefact says why.** The per-step traces (`traces.jsonl`, kept in
every replica's home) show the worker's first request differing across replicas by hundreds of
characters (step-1 `prompt_tokens` 6190 / 6172 / 6357 / 6177) and the model's step-1 output already
differing in all six pairs. The component that differs is the **plan**: `chimera solve` runs the
planner by default, `run.py` did not pass `--no-plan`, and `Planner.plan` samples at a **hard-coded
0.2** that `CHIMERA_TEMPERATURE` does not reach. So "T = 0 for every solve" was true of the worker
and false of the call that wrote the worker's first prompt — four T = 0 workers seeded by four T = 0.2
plans, and the divergence is ordinary sampling, not the route. **The cell is void — apparatus, not
phenomenon** (§2aa: config read from the artefact, never from the flags one meant to pass; §2ad: the
component's own defaults are part of the experiment).

Two smaller things the traces settled, both kept: (a) step-1 `cached_tokens` is the nonce's real
signature — SHARED r2 and r4 had 5,376 of ~6,170 tokens served from cache on their *first* request
(the system prefix shared across runs), NONCE r1 had 0 — but SHARED r1 and r3 also had 0 (a cold or
evicted cache), so on n = 1 the signature does not discriminate the nonce from a cold start; (b) the
run-level hit rate registered as M1 was the wrong quantity, as Amendment 1 said, and the per-step one
is on the trace.

**What replaces it, registered before running.** The same cell with the planner off — `--no-plan`,
which is also the factorial's bare arm — so the worker's first request is byte-identical by
construction. **Gate M0, checked from the artefact before anything is read:** the sha256 of the
recorded task text and the step-1 `prompt_tokens` must be identical across all replicas; if either
differs the cell is void again and the reason is found before money is spent on more. Then k = 4,
SHARED, T = 0, pinned to `AtlasCloud`, written to a separate file so the voided cell stays on the
record. Reading rule unchanged from Amendment 1. Spend so far US$ 0.50; the four solves ≈ US$ 0.40;
ceiling unchanged at US$ 5.
