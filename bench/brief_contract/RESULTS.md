# Results — H10: the brief contract, stopped at the pilot gate

2026-09-25 · pre-registered in `PREREGISTRATION.md` (commit `e38b3434`, before any call) · model `openrouter/deepseek/deepseek-v4-flash-0731` pinned to DeepInfra (every call answered there) · **US$ 0.064** at the conservative price, 131 calls · raw rows in `results/pilot.json`.

## The decision under the frozen rule: uninformative

The registered gate was: *if A's pilot rate is at most 10% of its live runs (1 of 20 or fewer), the instrument cannot show a halving; report uninformative and spend nothing more.*

| arm A pilot (20 tasks × 1, not reused) | |
|---|---:|
| runs / halts | 20 / 0 |
| **invented requirement (any out-of-scope unit)** | **1/20 = 5%** (Wilson 95% 0.9%–23.6%) |
| out of scope in code only (no tests, no docs) | 0/20 |
| success (fixture tests + hidden check) | 20/20 |
| subtasks per run | 1.00 (20/20 briefs had one subtask) |
| calls per run | 6.55 |
| conservative US$ per run | 0.0032 |
| seconds per run | 33 (max 92) |
| cache-read share of prompt tokens | 83% (405k of 486k) |

At p_A = 0.05 the registered formula asks for **909 paired items** (k = 46 replicas per task) to see B = A/2. The gate fired, so **no arm B or C run was made**, there is **no McNemar test**, and **nothing is decided about the contract**. It is neither shown to help nor shown not to: the instrument, on this model and these tasks, has almost nothing for it to remove.

## Why the base rate is low — the briefs, read

The instrument itself worked (PROTOCOL §4, §2e): every run's edits reached disk through the adapter, the detector's units agree with `git status` in all 20 runs (the only extra paths are `__pycache__`), it accepted two legitimate imports and one helper the fix calls (`cli.py::_since_date`), and it caught the one run that went further. What keeps the rate low is what the shipped manager already writes.

1. **The shipped schema already produces the "only this" line.** `_DECOMPOSE_SYSTEM` asks for a `boundaries` field, and all 20 of A's briefs used it for a scope fence: *"Only modify the to_cents function; do not change other parts of money.py"*, *"Only change the load() function in tally/store.py"*, *"Only modify tally/cli.py"*. The contract's scope clause largely restates what the field already elicits, and the workers (under `WORKER_SYSTEM`'s *"Do exactly that task — nothing beyond the boundaries"*) obeyed it: no worker touched a neighbouring bug in 20 runs, although every module had one in plain view.

2. **The one invented requirement came from the manager, not the worker** — exactly the failure the contract names. On `dates_weekend` the brief read *output_format: "… plus any relevant test updates showing the fix passes"* and *boundaries: "Only change is_weekend and its directly related tests"*. The owner asked for neither. The worker obeyed its brief and added two tests. So the mechanism the plan describes (§2.8: every sentence of a brief becomes a requirement) is real here, but it happened once in twenty.

3. **The briefs paraphrase and add specifics, inside the allowed function, where a unit-level detector cannot see them.** Only 3/20 objectives quote the owner (common substring ≥ min(40, half the request)); none carries an "Only this:" line (not asked for in A). Many add requirements the owner never stated, all inside the named function: *"handle standard rounding rules (half up) … 0.295 should round to 30 … use Decimal if needed"*, *"Handle cases where total < num_shares"*, *"if description is not a string, handle it gracefully"*, *"date validation"*, *"use tempfile.NamedTemporaryFile"*. These are invented requirements in the plan's sense, and this bench's primary metric is blind to them by design (registered under "what this cannot show": behaviour changed inside an allowed function).

## What this says about adoption

- **The contract is not a candidate on this evidence**, for the manager prompt or for a `worker` module. The arm stopped before either was measured.
- If H10 is re-run, the pilot says where the effect could live, and the instrument would have to move there:
  - **brief-level invention**: count the sentences of each brief that the request does not support (a deterministic proxy: numbers, names, formats and file paths in the brief that are absent from the request), rather than the diff;
  - **less specified requests** ("the rounding in money is off"), where the manager has to choose the scope instead of copying the named function into `boundaries`;
  - **tasks the manager splits**: every A run here had one subtask, so the "extra subtask for tests or docs" path never opened.
  Any of these is a new pre-registration, not an amendment of this one.

## Two findings about the configuration, not the prompt

Both concern write-capable hierarchy workers, which no user reaches today (the router falls back on write-shaped tasks, and the API gives workers read-only tools). They matter only if S6 ships writing workers.

1. **The shipped per-delegation budget cannot carry a writing worker.** With the coding registry's 28 tool schemas a worker call is about 5k tokens, so the default `delegation_budget` of 8,000 cuts a worker off after one or two calls. This bench used 80,000.
2. **"Cut off" is not "did nothing" when a worker writes.** In 3/20 runs (`store_sorted`, `cli_since`, `cli_added`) the worker made the correct edit, kept checking, hit `max_steps = 6`, and was rejected as cut off; the orchestrator then took the `workers_failed` fallback and answered with a one-shot top-model reply that never saw the edit, while the edit stayed on disk and passed its check. For read-only workers that fallback is right; for writing workers the answer would contradict the disk.

## What this arm cannot show

Everything the pre-registration listed, plus: the effect of the contract at all (no B or C run was made); whether the 5% is stable (one run per task, so no replica floor); and whether other models write more expansive briefs (one model, one provider, one day).

## Spend

US$ 0.064 of the US$ 2.00 cap (conservative price: 0.10 per M input, 0.40 per M output, cache reads at the full rate; 486k prompt and 39k completion tokens). No main run.
