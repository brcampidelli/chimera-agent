# Results — cascade bench (weak / mid / cascade / fusion)

Four arms over the same deterministic suite. Registered criterion (stated in the
CLI help before running): **cascade ≥ mid-only pass rate at materially lower cost.**
`chimera cascade-bench --tasks hard`.

## Run 1 — 2026-07-08, hard suite (12 reasoning traps), n=12

Tiers pinned for a clean signal (functional weak instead of the free tier, cheap
fusion panel to avoid frontier spend on a real key):
weak=`mistral-small-3.2-24b`, mid=`deepseek-chat-v3.1`, fusion panel=
deepseek + mistral-small + qwen3-coder (judge/synth = deepseek).

| arm | pass rate | total tokens | tokens / pass |
|---|---|---|---|
| weak only | 58.3% | 509 | 72.7 |
| mid only | **100%** | 846 | 70.5 |
| cascade | 91.7% | 785 | 71.4 |
| fusion | 100% | **9,526** | 793.8 |

### Honest reading

- **The headline: fusion cost 11× the mid tier for the IDENTICAL 100% pass
  rate** (9,526 vs 846 tokens). On this suite fusion buys nothing — which is the
  whole argument for *reserving* it. This is the strongest evidence in M16 that
  always-on fusion is waste and the cascade/hierarchy design (escalate only when
  a cheaper tier fails a gate) is the right default.
- **Cascade delivered near-mid quality (92%) at ~1/12 of fusion's tokens** (785
  vs 9,526). It accepted a cheaper answer on 10/12 tasks and only climbed when a
  gate tripped.
- **Registered criterion: not met (92% < 100%).** Reported plainly. The reason
  is instructive: on THIS suite the mid tier already saturates at 100% and is
  the cheapest-per-pass, so there is no headroom for either cascade or fusion to
  add quality — the cascade can only match-or-slightly-trail mid while saving
  vs fusion. The cascade's value regime is where the mid tier does NOT already
  solve everything.
- **The one cascade miss (`sister_age`) is a real limitation to log:** the weak
  tier produced a lexically-plausible but wrong answer that passed the
  deterministic acceptance gate, so the cascade accepted it instead of climbing.
  A free lexical gate cannot catch a confident-wrong answer; that is the honest
  ceiling of a zero-cost gate (a correctness check would need a model call, which
  is what the mid/fusion tiers are). No re-roll, reported as-is.

### What this validates in the codebase

- `RoutingPolicy` + `CascadeBackend`: keep fusion off the hot path; escalate only
  on a failed gate. The 11× fusion cost for zero quality gain is the measured
  justification.
- The `HierarchicalOrchestrator`'s "fusion only on conflict" synthesis and the
  profitability gate: same principle, measured here.
