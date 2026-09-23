# The Manager with the diff — pre-registration

Follow-up named in `bench/manager_p/RESULTS.md`. Written 2026-09-23, **before any call**.

## Question

`bench/manager_p` showed the Manager — shown the worker's prose only — approves 5/246 true and 1/139
false claimed successes: it asks for the files it cannot see. In production, on a coding turn, the solve
loop hands it what the attempt changed on disk (`autonomous.py`, `<<what-this-attempt-changed-on-disk>>`).
**With that evidence, does the Manager tell a true success from a false one?**

## Corpus

The same 385 claimed successes (`bench/manager_p/results/corpus.jsonl`; 246 true / 139 false by the oracle,
≥ 0.8). For each, the run's sandbox (`~/harness-bench/data_try6/sandbox/arm-<arm>-r<rep>/…/oc-bench-v2-<task>-arm-<arm>-r<rep>-*/workspace`,
the latest timestamp when a task was re-run) gives the files as the run left them.

**The evidence, reconstructed.** `before` = the workspace's `in/` as found plus an empty `out/`; `after` =
the workspace as found. Both captured with the production `WorkspaceGuard.snapshot()`; the context is built
by the production functions — `diff_snapshots(...).audit_summary()` and `unified_diffs(...)`, bodies cut at
`_JUDGE_DIFF_CHARS` (4 000) — into the production wrapper, byte for byte. Two limits, stated before running:
an edit the agent made inside `in/` is invisible (tasks write to `out/`); and the diff is of the whole run,
where production shows the last attempt's (identical for a single-attempt run). A row whose sandbox is
missing is dropped and counted.

## Arms

* **M_diff** — the production `Manager` (shipped prompt and parser), `openrouter/deepseek/deepseek-v3.2`,
  `review(task, answer, context=<the evidence>)`. Hosted; the run stops at US$ 3 metered.
* Reference: **M_prose** from `bench/manager_p` (same model, same rows, no context): 5/246 vs 1/139.

## Metrics

* **Primary:** discrimination = approve rate on true successes − approve rate on false ones (TPR − FPR),
  with a task-clustered bootstrap 95% interval (2000, seed 7).
* TPR and FPR with Wilson intervals; the same within the 10 tasks that have both classes (pooled over them);
  abstentions and errors counted; spend.

## Reading (fixed now)

* **The diff makes the Manager a discriminator** — TPR − FPR ≥ 0.20 with the lower bound > 0.
* **It approves more but does not discriminate** — TPR rises, the interval of TPR − FPR contains 0: the diff
  cures the "I cannot see the files" refusal and adds no judgment.
* **Nothing moves** — TPR stays under 0.10.

No product change follows from any reading by itself: the Manager already receives the diff in production.
What changes is what the record may say about the Manager as a gate.

## Prediction (written before running)

The second reading: approvals rise a lot on both classes (TPR ≈ 0.6, FPR ≈ 0.5) and TPR − FPR stays inside
±0.15 — the diff proves work happened, not that it is right, and a false success in this corpus is
usually a run that did write files, just wrong ones.

## What this cannot show

Other executors or Manager models; diffs inside `in/`; per-attempt diffs of multi-round runs.
