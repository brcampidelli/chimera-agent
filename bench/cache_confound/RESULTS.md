# Results — the cache question closes as not isolable on a hosted route; what the instrument showed on the way

**2026-09-15.** Study 19, item A1, against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its two
amendments. Model `openrouter/deepseek/deepseek-v3.2`, the factorial's; every solve served by the
route **`AtlasCloud`**. Total spend **US$ 0.77** on 9 solves (ceiling US$ 5). Reader `read.py`;
driver `run.py`; rows in `~/cache-confound/check.jsonl` (the instrument check and the voided cell)
and `~/cache-confound/noplan.jsonl` (the cell that counts).

## The free half: nothing we published records the cache or the route

Checked before any solve (registration §"Why it is our problem"): `hb-driver.jsonl` (556 rows), the
factorial's 557 result JSONs and `.chimera/runs.jsonl` carry neither `cached_tokens` nor `provider`.
`StepLog.cache_hit_rate` existed in memory and was never written to disk. So for every paired number
in `bench/` — the factorial's noise floor, `fusion_paired`, `edit_tools`, the seed floor of §2m —
whether it was produced under cache hits, and by which of the several providers that serve this
model id, is **unknowable from what we kept**. That is the first result and it cost nothing.

**Shipped so far it cannot happen again:** every attempt receipt now carries `cache_read_tokens`
(the sum the route reported; `None` when it reported nothing, never 0) and `provider` (the route
that served the first step); `CHIMERA_PROVIDER_ORDER` pins a request to named routes with fallbacks
off; `CHIMERA_TEMPERATURE` overrides the worker loop's temperature. Eight tests.

## Instrument check (Amendment 1): the nonce is not "cache off", and no hosted lever is

Three solves of `042`, unpinned, all served by `AtlasCloud`:

| solve | run-level hit rate | step-1 `cached` / `prompt` | fingerprint | score |
|---|---:|---:|---|---:|
| SHARED r1 | 0.842 | 0 / 6,190 | `b3e3fd19` | 0.74 |
| SHARED r2 | 0.863 | **5,376** / 6,172 | `7c78ad5e` | 0.74 |
| NONCE r1 | 0.831 | 0 / 6,179 | `9c89b455` | 0.74 |

Condition 1 passed (the route reports cache usage). Condition 2 failed on the registered metric: the
nonce did not move the run-level hit rate (0.831 against 0.842–0.863). The traces say why, and it is
structural. A multi-step agent re-sends its own transcript at every step; the provider serves that
**intra-run** prefix from cache whatever the system prompt says, and that is ~85% of all prompt
tokens. The nonce acts only on the **cross-run** share of the system prefix — visible on step 1,
where SHARED r2 read **5,376 of 6,172 tokens** from a cache the previous run had warmed and NONCE r1
read 0 — a sliver of the run. The paper's "cache off" is a server flag with byte-identical inputs;
on a hosted route anything that defeats the cache changes the bytes, and byte-identical replicas are
cacheable by definition. **The registered NONCE condition cannot be implemented here.** The grid was
not run, as the registration said.

## The voided cell (Amendment 2): four "T = 0" replicas that were not

Four SHARED replicas of `042` at `CHIMERA_TEMPERATURE=0`, pinned from r3 on, all `AtlasCloud`:
fingerprints `b3e3fd19` / `7c78ad5e` / `b193f1ae` / `c72960ac`, scores 0.74 / 0.74 / 0.40 / 0.74,
tool sequences parting at calls 6, 3 and 2. From the receipts that reads as *byte-identical T = 0
runs diverge on this route*. **The per-step traces say the inputs were not identical.** Step-1
`prompt_tokens` were 6,190 / 6,172 / 6,357 / 6,177, the recorded task text differed by hundreds of
characters, and the model's step-1 output differed in all six pairs. The component that differed is
the **plan**: `chimera solve` runs the planner by default, `run.py` did not pass `--no-plan`, and
`Planner.plan` samples at a hard-coded 0.2 that `CHIMERA_TEMPERATURE` does not reach. Four T = 0
workers, seeded by four T = 0.2 plans, diverging at step 1 for the ordinary reason.

Two lessons, both already in the rules and both missed anyway: the config of a run is read in its
**artefact**, not in the flags one meant to pass (§2aa), and a component's own defaults are part of
the experiment (§2ad). The cell is on the record as void; nothing in it is a replicate floor of
anything. (The re-run's cleanup deleted r1's trace before it was moved aside — my mistake, the same
tag was reused; its step-1 numbers above were read before the deletion. r2–r4 and NONCE r1 are kept
under `~/cache-confound/voided/`.)

## The cell that counts: SHARED, T = 0, `--no-plan`, pinned to `AtlasCloud`, k = 4

**M0 — first-request identity, from the artefact, before anything else was read: IDENTICAL.** The
recorded task text hashes to `0d722b78` and the route counted **5,930** prompt tokens on the first
request, on all four replicas. This is the paper's setting realised on our route: the same bytes,
T = 0, the cache **on** — on r2, r3 and r4 the first request was served **5,888 of 5,930 tokens
(99.3%) from cache**, warmed by r1, which read 0.

| replica | step-1 `cached` / `prompt` | run-level hit rate | tool calls | first call that differs | fingerprint | score | US$ |
|---|---:|---:|---:|---|---|---:|---:|
| r1 | 0 / 5,930 (cold) | 0.821 | 22 | — | `594493ba` | 0.74 | 0.044 |
| r2 | 5,888 / 5,930 | 0.826 | 28 | call 3 (vs r1) | `63759b7b` | 0.74 | 0.055 |
| r3 | 5,888 / 5,930 | 0.912 | 35 | call 2 (vs r1, r2) | `58aa3e9b` | 0.74 | 0.072 |
| r4 | 5,888 / 5,930 | 0.941 | 42 | call 6 (vs r1) | `ef0cf55f` | 0.40 | 0.098 |

**M2 — 0 of 1 cells identical: four distinct tool sequences, four distinct fingerprints.** And the
traces put the divergence earlier than the tool names do: the model's **first response** differs in
all six pairs — the same opening sentence for ~15 tokens, then four different todo lists — including
between r2, r3 and r4, whose cache state was the same (99.3% warm). **M3 —** scores 0.74, 0.74, 0.74,
0.40; within-cell SD **0.147**. For the same task in the factorial at T = 0.2, the bare arm scored
0.40, 0.74, 0.40 (SD 0.16) and the eight arms' RMS SD was 0.13: on this task and this route,
**T = 0 bought no reproducibility that T = 0.2 lacked.** (k = 4 against k = 3, one task — an order of
magnitude, not a comparison.)

**Reading, by the rule written in Amendment 1 before these solves:** fewer than 4/4 identical →
byte-identical T = 0 runs diverge on this route; the paper's dichotomy is not testable here; every
replicate floor we have published includes hosted nondeterminism of unknown composition. Two things
sharpen it. First, the paper's baseline — cache **off** reproduces 800/800 — has no counterpart on
this route, because the route has no off; and its "cache on" condition, which we did realise
exactly (99.3% of the first request from cache), diverged 4/4 where the paper saw 36.2%. Second,
the three warm replicas differ **among themselves** on identical bytes and identical cache state, so
whatever the cache contributes sits on top of a nondeterminism the cache does not explain —
batching, kernels, or routing inside the provider, none of which the receipt can see. The cache's
own share is therefore **not isolable here**, which is what the registration's first row says.

## Decision — row 1 of the registered table, reached through the instrument

The registered decision needs a NONCE cell, and the instrument check showed that no NONCE cell can
exist on a hosted route. That is row 1 ("the paper's dichotomy does not transfer to this route; the
T = 0 replicate floor is recorded and the cache question is **closed as not isolable here**"), reached
through the instrument rather than through M2. The paper's 36.2% is a statement about a self-hosted
stack with a server-side switch; on this route the two conditions it compares cannot both be built.

**What this does and does not do to the archive.** Every published paired number was measured at
T = 0.2 on a route nobody recorded, and its replica-to-replica noise is the sum of sampling, hosted
nondeterminism and whatever the cache contributes — three terms this study cannot separate and no
experiment on this route can. The archive's nulls stand as measured (their intervals already contain
that noise); what changes is the caption: *within-cell SD on `deepseek-v3.2` via OpenRouter, route and
cache state unrecorded*. From now on the route is on the receipt.

## What ships

- `cache_read_tokens` and `provider` on every attempt receipt (`Attempt`, `AttemptReceipt`).
- `CHIMERA_PROVIDER_ORDER` — route pin with fallbacks off, for measurements.
- `CHIMERA_TEMPERATURE` — the worker loop's temperature, documented as reaching the worker only.
- `CHIMERA_PREFIX_NONCE` — kept, documented as what it is: a cross-run-share switch, not "cache off".
- `bench/cache_confound/`: the registration with two amendments, the driver with the M0 gate, the
  reader, and this file.

## What this cannot show

- Anything at T = 0.2 (the archive's temperature), by design.
- Anything about Anthropic routes (different mechanism; the earlier `claude-haiku-4.5` probe surfaced
  no cache tokens for reasons never established).
- Whether `AtlasCloud` sits in front of DeepSeek's own cache: `cache_read_tokens` is what the route
  reports, and M1 is a lower bound on engagement.
- Whether the pinned request (`extra_body.provider`) is treated identically to the unpinned one by
  the route: r1, r2 and NONCE r1 of the instrument check were unpinned and happened to land on
  `AtlasCloud`; every later solve was pinned. Recorded, not tested.
- With k = 4 on one task, a divergence rate: four replicas say whether byte-identical T = 0 runs
  reproduce on this route, not how often.
