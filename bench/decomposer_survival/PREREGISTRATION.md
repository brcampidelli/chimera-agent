# Pre-registration — S30-60: verbatim survival of user constraints in hierarchy decomposition

**Registered before any model call.** Study 30, item S30-60. Budget: **US$ 0**. The local Ollama run is deliberately not started on this machine; its result remains owed.

## Question

When Chimera's real hierarchy decomposer converts a user's task into `TaskSpec` objects, do explicit user constraint sentences survive verbatim in at least one sub-task's worker-facing instructions? The concern is that a constraint such as “do not touch the migrations” or “keep the public API” may be paraphrased away.

## Corpus and unit

The frozen corpus in `corpus.json` contains **30 tasks**. Each task has exactly two explicit constraint sentences (60 constraint sentences total), following the user's requested range of one to three. Each sentence is a standalone instruction included in the user's task text. Tasks cover code changes, research, documentation, tests, data, deployment and review without relying on project-specific context.

The denominator is all individual constraint sentences in the corpus, not tasks. A sentence survives if its registered form occurs in at least one decomposed subtask's `objective`, `output_format`, or `boundaries`. A constraint may be duplicated across subtasks but counts only once. The decomposer's raw response and parsed `TaskSpec`s are retained in the readout for audit.

## Registered normalization and scoring

Before matching, apply only this normalization to both sentence and candidate text: Unicode NFC; replace each run of whitespace with one ASCII space; strip leading/trailing whitespace. Matching is case-sensitive and punctuation-sensitive substring matching. No lowercasing, punctuation removal, stemming, semantic equivalence, or other cleanup is allowed. Thus the primary measure is literal string survival, with the narrow normalization above.

For constraint `c`, survival is 1 iff the normalized full sentence is a substring of normalized text from at least one subtask's three worker-facing text fields; otherwise 0. The headline is `survived_constraints / total_constraints`, with a per-task breakdown and the exact matched field(s) in the JSON readout. Failed decomposition or an empty spec list scores all of that task's constraints as lost; it is recorded as a decomposition failure, not dropped from the denominator.

## Model and procedure

The real run uses Chimera's shipped `HierarchicalOrchestrator.decompose()` and its normal `_DECOMPOSE_SYSTEM` prompt, with the pluggable gateway backend configured for **`ollama_chat/qwen3:4b`**. No hand-built decomposition, parser replacement, or constraint-repair layer is used. Each corpus task is submitted once, in corpus order. No retries are added by the harness beyond the product decomposer's own existing invalid-JSON repair behavior. No model call is made by the stub test.

The machine's Ollama service is not running. We will not start it and will not run the model in this change. The real measurement is therefore **owed**, not zero, not a failed measurement, and not inferred from the stub.

## Decision rule

If observed constraint survival is **below 1.0**, a deterministic pass-through of the original constraint sentences must ship enabled by default in a subsequent change, ahead of or alongside the decomposed worker-facing instructions. That pass-through is **not built in this change**. A 1.0 result does not trigger that rule on this corpus; it does not establish general reliability outside these 60 registered sentences.

## Harness and test

`run.py` exposes the real Ollama backend and an injectable backend seam for deterministic tests. The pytest test in `tests/` invokes the harness with a stub that returns one spec carrying one registered constraint and omits another. It asserts the readout's schema and one-survived/one-lost scoring. The test is a harness check, not a model result.

## Reproducibility

The preregistration is committed before the corpus/harness are added and, critically, before any model call. The corpus and harness identify this file and its commit. The run writes JSON to stdout or an explicitly supplied output path; it never starts Ollama, installs a model, or spends money on its own.
