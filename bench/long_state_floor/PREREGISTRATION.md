# M3 — does our local backend give the same number twice on a long state? Pre-registration

*2026-09-23, written before any replay ran. Study 24, item M3 (`bench/PLAN-study24-jev-practice.md`).
Local only (`qwen3:4b` through the shipped `LocalLogprobBackend`), US$ 0.*

## Why

**Three independent practitioner sources say Jev varies between identical runs:**
- Kellogg, ±0.1–0.3 on marginal items;
- `jev-fraud`'s README;
- the vendor cookbook quoted by Flavio Copes.

**Our record says otherwise, but only for short states:**
- "0 flips in 55×5" in `bench/jev_decisions` §3;
- study 21's "k samples measure nothing".

`spot_noul` and `manager_p` then read nulls on **long** states without measuring a floor of their own. If our own instrument moves on a long state, those nulls were read against the wrong floor.

**What is measured here: our instruments, not Jev's.** Replaying Jev would cost under a cent, but publishing a Jev benchmark number is what TypeSafe's terms rule out. So the practitioners' claim about Jev stays unmeasured by us, and says so.

## Items

The **30 longest states of JevBench's public hard tier**:
- **Source:** MIT, at the commit `bench/jevbench_local` pinned (`2fa63fa`, same file hashes).
- **Length:** by the prompt token count `bench/jevbench_local` recorded, 1,100–3,900 tokens.
- **Questions:** each item's own question, mapped exactly as `bench/jevbench_local` maps it.

## Procedure

- **Rounds:** 5 rounds. Each round asks all 30 items once, in an order shuffled per round (seed 24).
- **Request:** the request is byte-identical across rounds: the shipped instrument with `num_ctx` 16384, `temperature` 0.
- **Why shuffle:** consecutive items change what Ollama can reuse from the previous prompt's prefix, the variable study 21 saw flip 5–6 of 777.

## Outcomes

- **Per item:**
  - the maximum absolute difference, across the 5 rounds, in the probability of the item's first-round argmax label;
  - whether the argmax ever changed (a flip).
- **Summary:**
  - the median and the maximum of that difference over the 30 items;
  - the number of items with a flip;
  - items with no reading in some round but not in others.

## Predictions

- **0 flips in 30.**
- **Maximum |Δp| ≤ 0.02.** Greedy decoding with logprobs read at a fixed position is deterministic up to numerics and prefix reuse.

## Decision rule

- **If flips ≤ 1 and the maximum |Δp| ≤ 0.02:** our "the local backend does not move on replay" extends to long states. The `spot_noul` and `manager_p` nulls stand as read. The skill's new check, "ask the unchanged question again", keeps its reason: it guards any instrument, not ours in particular.
- **If either fails:** the floor is published beside those two nulls, and each is re-read against it in its own follow-up.

## What this cannot show

- Jev, or any hosted route.
- Other models and quantisations.
- Temperature above 0.
- A different Ollama build: the build is recorded in the rows.
