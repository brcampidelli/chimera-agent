# The envelope spot check as a local Noul — results

2026-09-23 · pre-registered in `PREREGISTRATION.md` (committed before any call) · local
`qwen3:4b@Q4_K_M`, `num_ctx 16384`, US$ 0 · 92 envelopes, 0 without a number, every one passing the
corpus's instrument check · rows in `results/spot_noul.jsonl`, summary in `results/summary.json`.

## Verdict: **null** — no shadow arm; the spot check keeps the hosted auditor

| | local Noul, `p ≥ 0.5` | reference: hosted DROPPED-only (`bench/blind_audit`) |
|---|---:|---:|
| `middle` — the plant was cut | **0 / 23** | 23 / 23 |
| `middle_clause` — a clause was cut | 0 / 23 | 20 / 23 |
| `head` — nothing cut | 0 / 23 | 6 / 23 |
| `none` — no plant | 0 / 23 | 5 / 23 |

**Primary: AUROC 0.655, 95% [0.538, 0.759]** — below the 0.85 the rule required, and the operating
point fails both halves. The model wrote "no" on all 92 envelopes.

There is *some* ranking: median `p` 0.104 when the plant was cut against 0.049 when there was none
(max 0.42 against 0.13). Not a reading anyone could threshold: the whole distribution sits below 0.45,
and the interval's lower bound is near chance.

My prediction — AUROC between 0.55 and 0.75 — held.

## The apparatus check that mattered

Prompts ran 5 200–9 800 tokens. Ollama's default context is 4 096 and it truncates a longer prompt
**without an error** — every call here would have been read on a cut prompt, and the number would
have looked like a model result. The runner set `num_ctx` on the request and checked
`prompt_eval_count < num_ctx` on every response before using it (§2ad). Worth knowing for any
future local decision on a long state: the product's `LocalLogprobBackend` does not set `num_ctx`,
which is right for the governance question (a shell action is short) and wrong for anything long.

## What changes

* Nothing in the product. The spot check stays on the hosted auditor, whose DROPPED-only prompt
  already catches 23/23.
* `bench/PLAN-study22-system-one.md` §5 is corrected: "adopt, shadow first" becomes *not with a local
  4B*.

## What this cannot show

Nothing about a larger local model, about reading the label after a short reasoning trace (study
20's L2 arm: that collapses `p` to 0/1), or about a hosted model read by logprobs. Nothing about
summaries a model wrote.
