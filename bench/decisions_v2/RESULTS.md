# Study 22, phase 1 — what N questions cost, and what a cache hit costs

2026-09-23 · local only (Ollama `qwen3:4b@Q4_K_M`, RTX 5070) · US$ 0 · `measure_fanout.py` ·
raw rows in `results/fanout.json`.

The gate the roadmap set for phase 1 was "latency of N questions vs 1 on qwen3:4b measured". This
measures time, not accuracy: the three Nouls are illustrative instruments with no map.

## Setup

55 states — the governance bench's two-sided corpus (`two_sided_items`, easy + ambiguous). Per
state, three passes in order: the shipped governance Choice alone (no cache); `decide_many` over the
Choice plus three atomic Nouls (*remove files?*, *send data over the network?*, *write outside the
project?*) on a fresh cache; the same four again on that cache. Model loaded before timing.

## Numbers

| pass | median | p90 | mean |
|---|---:|---:|---:|
| one question | 0.322 s | 0.340 s | 0.323 s |
| four questions (`decide_many`) | 1.263 s | 1.315 s | 1.266 s |
| four questions, cache hit | 0.0001 s | 0.0001 s | 0.0001 s |

* **Four questions cost 3.92× one.** Linear, as expected: each question is its own instrument and
  the state sits *after* each question's instructions, so nothing is shared between calls. Sharing
  the prefix (state first) is a different instrument — a new hash, no map — and would need the
  governance bench re-run before the shipped map could apply to it. Not done here.
* **A cache hit is ~0.1 ms**, 220/220 hits on the second pass, 0 halts in 385 reads.
* **One call is 0.32 s**, not the 0.75 s the bench recorded: study 21's single kept client removed
  the per-call handshake, which the bench's number included.
* The governance choice was the same on the uncached and the cached-pass reads in 55/55 states.
* The Nouls answered both ways (deletes 17 yes / 38 no, network 22 / 33, outside 9 / 46) — a
  question that answered one way on every state would have been a parse artefact, not a reading.

## What this does not show (§2q)

* Nothing about whether the three Nouls, combined in code, rank danger as well as the one Choice —
  that is a bench with labels and a map per Noul, phase 3.
* Nothing about hosted backends: there, N questions are N calls billed each, and the cache is the
  only saving.
* Nothing about the cache hit rate in real use. The governance surface repeats commands often
  (`git status`, `pytest -q`), but no log counts how often; the decision log (phase 2) is what will.
