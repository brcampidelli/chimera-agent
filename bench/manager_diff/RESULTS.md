# The Manager with the diff — results

2026-09-23 · pre-registered in `PREREGISTRATION.md` (committed before any call) · 385 claimed successes
(246 true / 139 false by the oracle) · production `Manager`, `deepseek-v3.2`, **US$ 0.103**, 0 errors, 0
abstentions · evidence built by the production functions from the run sandboxes · rows in `results/`.

## The pre-registered number

| | approved true successes (TPR) | approved false successes (FPR) | TPR − FPR [95%, task-clustered] |
|---|---:|---:|---:|
| prose only (`bench/manager_p`) | 5/246 = 0.02 | 1/139 = 0.01 | ≈ 0.01 |
| **with the diff** | **47/246 = 0.19** [0.15, 0.24] | **2/139 = 0.01** [0.00, 0.05] | **0.177** [0.087, 0.268] |

None of the three registered readings fits exactly. The interval excludes zero, so the diff does make the
Manager discriminate, but 0.177 is under the 0.20 bar for "a discriminator"; TPR is far above the 0.10 of
"nothing moves". The honest label: **a precise, very strict gate** — it almost never approves a false success
(2/139) and it rejects four in five true ones. My prediction (TPR ≈ 0.6, FPR ≈ 0.5, no discrimination) was
wrong on both counts: the diff did not make it lenient, and it did make it discriminate.

## A third of the evidence was my apparatus, not the run (§2e — read before believing)

The first raw rejection read *"the diff shows 'no productive change'"* on a run the oracle scored 1.0. The
registered limit — an edit inside `in/` is invisible to the reconstruction — is not a corner case in this
corpus: the repair tasks (`040`–`045`, `082`, `086`, `087`, …) edit the repository under `in/`.

| evidence the Manager was shown | rows | TPR | FPR |
|---|---:|---:|---:|
| shows a productive change | 262 | 47/197 = 0.24 | 2/65 = 0.03 |
| says "no productive change" (the in/ blind spot) | 123 | 0/49 | 0/74 |

On the 123 rows the Manager was shown evidence that *nothing happened*, and it rejected all of them — correctly,
for the evidence it had. Those rows measure my reconstruction, not the Manager. On the 262 where the evidence is
real (descriptive, not pre-registered): approves 24% of true successes, 3% of false ones.

## Why it rejects true successes — the raw replies

On evidence that shows the work: *"the verification command must be run from the specified directory"*, *"does
not provide proof that pytest actually ran"*, *"the output was truncated"* — objections about the **report**, not
the work, several of them wrong about the task (the oracle scored those runs 0.97–1.0). The Manager grades the
worker's account of itself; given a diff it stops demanding files and starts demanding proof of process.

## What this means for the product (not changed here)

In the solve loop the Manager decides an attempt only when no executable verifier ran (`autonomous.py`: the
attempt is decided by "verifier" when a command exited 0, otherwise "diff+manager" or "manager"). On that path,
this measurement says a correct attempt is rejected three times in four. That is a finding about the production
gate worth its own study — how often the manager path decides in real runs, and what a rejection costs there
(a retry, or a revert) — and it is not something this file changes.

## What changes

* `bench/PLAN-study22-system-one.md` §5 — the Manager row gains this result.
* Nothing in the product.

## What this cannot show

Other Manager models; the diff of edits inside `in/` (the reconstruction's blind spot, 123 rows); per-attempt
diffs of multi-round runs; how often the manager path decides a production run.
