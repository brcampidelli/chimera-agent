# The Manager's P(approved) against the oracle — results

2026-09-23 · pre-registered in `PREREGISTRATION.md` (committed with the corpus, before any call) ·
385 claimed successes from the harness factorial, 139 false by the oracle (≥ 0.8 = pass) · local arm
`qwen3:4b@Q4_K_M` (US$ 0) · Manager arm `deepseek-v3.2`, **US$ 0.064** · rows in `results/`.

## Verdict: **null** — no record-only number is shipped; the Manager stays a word

### P_local — the number

| | value | pre-registered bar |
|---|---:|---:|
| AUROC, all 385 (task-clustered 95%) | **0.644** [0.476, 0.780] | ≥ 0.70, lower bound > 0.5 |
| AUROC **within task** (10 tasks with both classes) | **0.525** [0.386, 0.650] | ≥ 0.60, lower bound > 0.5 |
| at p ≥ 0.5: approves true / false successes | 0.62 / 0.42 | — |

Fails both bars. What little ranking exists overall comes from **which task** a row belongs to, not from
the answer: within a task the number is at chance. My prediction (≈ 0.60 overall, ≈ 0.5 within) held.

### M_word — the Manager's own verdict (secondary)

| | approved |
|---|---:|
| true successes | 5 / 246 = 0.02 [0.01, 0.05] |
| false successes | 1 / 139 = 0.01 [0.00, 0.04] |

My prediction was the opposite — that it would approve most of both. **Read before written down (§2e):**
three raw replies, re-asked at US$ < 0.001:

* two of `011-code-debug` (oracle 0.86): *"REVISE: the Worker's result should be a concise statement like
  'Layer 5 fixed' without additional commentary"* — a misreading of the task;
* one of `016-code-repair-pytest` (oracle **1.0**): *"REVISE: the Worker's result does not provide the actual
  fixed code or progress.md content; it only describes actions taken"*.

The second is the mechanism. In these tasks the work is **in files**, and this arm showed the Manager the
worker's final prose only — so it asks for the artifacts it cannot see, on right and wrong work alike.
**The pre-registration was wrong to call that "as in production"**: on a coding turn the solve loop hands
the Manager the diff as context (`autonomous.py`, the `diff+manager` path), which this arm did not. So
this row measures *a Manager that reads only prose*, and it says that such a Manager cannot tell a true
success from a false one — it rejects both. It says nothing about the diff path.

## What changes

* Nothing in the product. No record-only spec; `Manager` returns a word as before.
* `bench/PLAN-study22-system-one.md` §5 is corrected with both numbers.
* A follow-up worth its own pre-registration, not run here: the Manager **with the diff as context** on the
  same 385 rows — the corpus has no diffs, so it needs the sandboxes that still hold them.

## What this cannot show

Nothing about other executors; nothing past 2 000 characters of task or answer (the run log's cut —
141 of 385 tasks were cut); nothing about the diff path (above).

## Note added 2026-09-24 — the replay floor on long states (study 24, M3)

This null was read without a replay floor of its own on long states. `bench/long_state_floor` measured one on the same instrument: **no label flips** in 22 long JevBench states × 5 identical rounds. But `p` moves by up to **0.036** (median 0.003), mostly in the middle of the scale. The requests are byte-identical and only the call order changes, so the likeliest cause is what Ollama can reuse from the previous prompt; that cause was not isolated.

A jitter that size can reorder items whose `p` differ by less than 0.036. It cannot create or erase an AUROC gap of the size reported above, so the null is expected to stand. A re-read against the floor is owed in a follow-up.
