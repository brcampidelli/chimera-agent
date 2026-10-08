# Wire reconciliation — synthetic results

**Run:** deterministic fake-backend harness, seed `3061`; US$0; no provider/network calls. 30 trials per fault class and 30 clean controls; each run generated five gateway exchanges. The mutation was injected into the steplog after the gateway wrote its append-only wire log.

| Outcome | Detected / trials | Rate | Two-sided 95% Wilson interval |
|---|---:|---:|---:|
| Omission | 30 / 30 | 100.0% | 88.6%–100.0% |
| Fabrication | 30 / 30 | 100.0% | 88.6%–100.0% |
| Altered copy | 30 / 30 | 100.0% | 88.6%–100.0% |
| **Pooled faults** | **90 / 90** | **100.0%** | **95.9%–100.0%** |
| Clean-control false positives | 0 / 30 | 0.0% | 0.0%–11.4% |

These results meet the expected behavior for the registered synthetic mutations but **do not meet the preregistered rule for enabling the feature**: 30 trials per category is below the required 100, and the individual-fault lower confidence bounds are below 0.90. Keep `CHIMERA_WIRE_LOG` OFF unless a separate validation satisfies every preregistered condition. These data say nothing about real providers, production frequency, shared faults or gateway bypasses.

Reproduce from the repository root:

```cmd
uv run --extra dev --extra desktop python bench/wire_reconcile/run.py
```

## Local-Ollama replication (Amendment 1) — 2026-10-08

**Run:** `ollama_chat/qwen3:4b` on the local GPU, `CHIMERA_WIRE_LOG=true`, cache off, one fresh
`CHIMERA_HOME` per run, ten fixed file tasks × ten replicas, `max_steps=4` (protocol in
`PREREGISTRATION.md`, Amendment 1; command in `RUN.md`). US$0. 100 pristine runs, **224 model calls**
(76 runs made 2 calls, 24 made 3), every run stopped as `final`, **0 protocol failures**. Wall time
1.76 h (mean 63 s per run, 11–146 s). 79 of the 224 steps carried more than one tool call. No
compaction fired (it was off), no closing call was made (no run reached `max_steps`), and no fallback
was configured. Raw artifacts: `results/ollama/<run>/` (`home/wire.jsonl`, `home/traces.jsonl`,
`meta.json`, the workspace) and `results/ollama.json` (the `inject` output). The mutated copies
(`results/ollama-mutated/`) are not committed: `inject` regenerates them from the seed.

| Outcome (paired copies of the same 100 runs) | Detected / trials | Rate | Two-sided 95% Wilson interval |
|---|---:|---:|---:|
| Omission | 100 / 100 | 100.0% | 96.3%–100.0% |
| Fabrication | 100 / 100 | 100.0% | 96.3%–100.0% |
| Altered copy | 100 / 100 | 100.0% | 96.3%–100.0% |
| **Pooled faults** (300 paired copies, not independent) | **300 / 300** | **100.0%** | 98.7%–100.0% (nominal; the copies share runs) |
| Clean-copy false positives | 0 / 100 | 0.0% | 0.0%–3.7% |

The numbers meet the letter of the enablement rule for **this** shape of run. Read them for what they
are, though:

- **Detection is close to guaranteed by construction here.** Reconciliation compares exact digests
  keyed by `wire_id`. Deleting a step leaves a wire record with no partner, a fabricated step carries
  an id the wire log never issued, and an altered digest cannot equal the one written at the gateway.
  On a run of ~2 calls none of the three can hide. 100/100 shows the **implementation is correct** on
  real runs and real files; it does not show the problem is hard, and it says nothing about faults
  outside the three registered mutations (a text edit that leaves the digests alone is still not
  detected — see the limits below).
- **The clean 0/100 is the informative number, and it was earned on easy runs.** Every gateway call in
  these runs was a step. The calls that are *not* steps — a closing call after `max_steps`, a
  loop-breaker ending, a compaction summary — never happened, so this run could not have shown them as
  false positives. That is what the follow-up (Amendment 3: long runs with compaction, a forced model
  switch and streaming) exists to measure, and **no VPS pilot rests on this result alone**.
- One model (qwen3, one family), one machine, one Ollama version. Not a claim about other providers.

## Limits found in review (2026-10-06)

- **"Altered copy" here means an edited digest field, not an edited text.** The mutation replaces the
  `response_digest` the step copied from the gateway. Reconciliation compares those copied digests;
  it does not recompute them from the step's retained (clipped) `content`, so an edit to the text
  that leaves the digests alone is **not** detected — pinned by
  `test_an_edit_to_retained_content_is_not_detected_documented_limit`. The 30/30 above is about the
  registered mutation and says nothing about content edits.
- **A cache hit makes no provider call**, so with `CHIMERA_CACHE` on its step carries no `wire_id`
  and is reported under `missing_wire`. Read such entries against the cache before calling them
  fabricated.
- The first implementation tapped only the blocking paths; the streaming path — the one the coding
  turn uses by default — was added in review. The synthetic run above drives `complete` only and is
  unchanged by that fix (re-run: identical counts).
