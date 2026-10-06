# Pre-registration — S30-55: does selecting raw turns beat extraction at a tight budget?

**Registered 2026-10-06, before implementation and before any model call.** Study 30, item S30-55. **The non-inferiority margin is −5 percentage points**, stated before the instrument or data are run. Spend: **US$ 0**. No model will be run as part of this change.

## Why

Memory extraction was adopted on safety evidence (poison 0, precision 1.0), not utility against reading the source history. The cited result, arXiv 2609.34227, reports that a typed-decision selector was non-inferior at tight k (LoCoMo difference 0.5 points; one-sided bound −3.0 against margin −5), improved LongMemEval by 9.1 points at k=3, and cost about 3,000× less to write. This study asks the narrower, local question: can reranking the same history candidates improve retrieval over the existing FTS order?

Extraction is a separate feature and is **not turned off or changed by this result**.

## Setup (frozen)

- **Corpus:** deterministic synthetic `history.db`, generated locally by `bench/memory_selection/generate.py`; no LoCoMo copy is known to be available locally, and no dataset will be downloaded. Thirty query items, each with one or more exact answer-bearing turns among thirty candidates. Distractors share query terms and are intentionally plausible. Generator seed and item text are committed with the harness.
- **Candidates:** exactly the same 30 `HistoryHit` rows for each item, inserted in identical order, same synthetic project and time range. Candidate membership is fixed independently of either ranker; this isolates ordering and does not measure candidate generation/recall.
- **Arms:** A, existing FTS5 `HistoryIndex.search` order; B, the same retrieved candidates ranked by a `qwen3:4b` System One decision through Chimera's local decision backend; optional C, extracted-memory recall only if the extraction path can be exercised offline at no implementation cost. C is descriptive and cannot replace A or B.
- **Budget:** k=3 returned turns. A and B receive the same query and candidate texts; ties preserve original FTS order. Reranking is opt-in and disabled by default.
- **Model:** `qwen3:4b` on the configured local Ollama endpoint, System One through `chimera/decisions/local.py`; no hosted backend or paid service. Calls are not made before owner invocation of the measurement command.

## Outcome and analysis

A query is correct when at least one of its k returned turns contains the item's registered answer string, case-insensitively. Primary metric is the paired difference in query-level accuracy, B minus A, across the 30 identical items. Report both proportions, the paired difference, and a one-sided 95% paired bootstrap lower bound (10,000 resamples, seed 3055). B is non-inferior only if the lower bound is **strictly greater than −5 pp**. Also report exact answer-bearing turn's mean reciprocal rank over all candidates, per-item outcomes, backend halts, and elapsed time. An unavailable local model is an incomplete run, not a zero score.

This synthetic set is a harness check, not a population estimate. The small n cannot establish broad non-inferiority; no significance claim or adoption decision beyond this prototype follows from a pass. Report failures and all 30 item rows without exclusions.

## Reproduction

Generate the database and run offline plumbing checks without a model:

```cmd
python bench\memory_selection\generate.py --out bench\memory_selection\history.db
uv run --extra dev --extra desktop pytest -q tests\test_memory_selection.py
```

The **measurement command owed** (do not run in this task) is:

```cmd
uv run python bench\memory_selection\run.py --db bench\memory_selection\history.db --model qwen3:4b --k 3 --out bench\memory_selection\results\run.json
```

That command requires a locally running Ollama with `qwen3:4b` available and uses the local System One backend. It is explicitly opt-in; invoking it is the owner's decision.

## Limits and decision

This does not compare extraction with reading history on real conversations, does not measure candidate-generation recall, and does not validate LoCoMo or LongMemEval. Synthetic phrasing and answer strings are authored and transparent. There is no extraction arm unless it is cheap, fully comparable and offline. Regardless of outcome, `CHIMERA_MEMORY_EXTRACT` stays enabled as currently configured; extraction is not disabled by this study.

No model call is made while implementing this work. Results are written only after the owner explicitly runs the measurement command; this registration will not be rewritten to fit them.
