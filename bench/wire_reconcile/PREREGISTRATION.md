# Wire reconciliation — preregistration

**Study:** S30-61, second-writer wire log vs. harness steplog
**Status:** Registered before implementation or fault-injection run.
**Cost ceiling:** US$0. Synthetic only. No network provider is called.

## Question

Can an append-only provider-gateway record, written independently of the harness steplog, identify records that disagree with what the harness retained? The wire log is an independent observation point, not proof that a provider itself is honest. If both writers share a fault or the gateway is bypassed, reconciliation may not detect it.

## Registered outcomes and mutation classes

The benchmark generates deterministic synthetic exchanges through a fake backend. For each exchange, the fake backend supplies a request and response; the gateway tap records digests, and the harness creates the corresponding steplog. Mutations are applied **after** these pristine artifacts have been written and before audit:

1. **Omission:** remove one steplog exchange/step while leaving the wire record intact.
2. **Fabrication:** insert a plausible but nonexistent steplog exchange while leaving the wire log unchanged.
3. **Altered copy:** change one retained step's request or response representation while leaving the wire log unchanged.
4. **Clean controls:** leave both artifacts unchanged.

One independent trial is one generated run, and one run has exactly one registered fault for fault trials. The seeded generator, mutation selection and audit are deterministic. The benchmark reports each class separately and pooled, with raw `detected / trials` and two-sided 95% Wilson score intervals. A detection is a nonzero reconciliation discrepancy for the mutated run; a clean run is a false positive if reconciliation reports any discrepancy. Detection sensitivity is not inferred from a hash-chain check alone.

The implementation run uses 30 trials per fault class and 30 clean controls (120 synthetic runs total). No exclusion or replacement is allowed after outcomes are observed; generation/runner errors are reported separately and count as protocol failure rather than silently dropped data.

## Analysis

For each fault class, report sensitivity and its two-sided 95% Wilson interval. For controls, report false-positive rate and the same interval. Also report aggregate fault sensitivity over the three equally sized classes. No significance test or post-hoc subgroup is planned. These synthetic results establish only behavior against the registered mutations, not prevalence or real-provider generalization.

## Absolute rule for turning the option on

The option MUST remain **OFF by default**. A deployment may explicitly turn it on only when all of the following hold:

- the wire file is on storage with access controls and retention appropriate for run metadata;
- the operator has reviewed that the log contains only request/response digests and non-secret correlation metadata (never message bodies, credentials, or authorization headers);
- a validation run has at least 100 clean controls with zero false positives, and at least 100 trials in **each** registered fault class with the lower bound of that class's two-sided 95% Wilson interval at or above 0.90;
- the wire file is append-only for the process (no in-place edits or truncation in normal operation), and the operator understands that reconciliation cannot establish gateway/provider truth if both sources are compromised.

Failure to meet any condition, including insufficient sample size or inability to keep the log protected, means leave it OFF. The small initial synthetic run is a correctness exercise and cannot satisfy this enablement rule.

## Planned synthetic run and external work

Run the deterministic fake-backend fault injector; no credentials or network access are permitted. Publish `RESULTS.md` with the observed counts and intervals regardless of outcome. A real local-Ollama replication is owed separately and must be run only after the synthetic artifacts and command are available. Its cost is expected to be US$0, but it is not represented as completed here.

## Amendment 1 — 2026-10-07, the local-Ollama replication (written before any model call)

The registration owed "a real local-Ollama replication" without fixing its runs, its model or its trial
count. Fixed here, before the first call. The outcome definitions, the four classes, the Wilson interval and
the enablement rule are unchanged.

1. **Pristine runs.** `chimera.core.agent.Agent.run` on `ollama_chat/qwen3:4b` (Ollama 0.32.0), through
   `LLMGateway` with `CHIMERA_WIRE_LOG=true`, `CHIMERA_CACHE` off, one fresh `CHIMERA_HOME` per run, and
   `trace_path=<home>/traces.jsonl`. Tools: `read_file`, `write_file`, `edit_file`, `list_dir` rooted at an
   empty per-run workspace (no shell, no network). `max_steps=4`, `temperature=0.2` (the agent default),
   `thinking=False`, `num_ctx=16384` on every call. Ten fixed file tasks (in `run_ollama.py`), each run with
   ten replicas in a fixed order: 100 pristine runs.
2. **Trials.** Each pristine run is copied four times — clean, omission, fabrication, altered copy — and the
   mutation is applied to the copy's steplog only; the wire log is never touched. So each class has 100 trials,
   each on a distinct run, and the classes are **paired** on the same runs (independence across classes is
   not claimed). Mutation choices come from `random.Random(3061)` in run order.
   - omission: delete one step chosen uniformly;
   - fabrication: insert, at a uniform position, a step with a fresh `uuid4().hex` `wire_id` and digests of a
     made-up request/response (well-formed, so it is not caught by a missing field);
   - altered copy: replace one step's `response_digest` with the digest of a different response.
3. **A run the model or gateway could not produce** (error, zero steps, or no wire record) is reported as a
   protocol failure, with its count, and is not replaced.
4. **Clean false positives are the point of this replication.** In the synthetic run every gateway call was a
   step. A real run can make gateway calls that are not steps (a retry, a compaction summary, a loop-breaker
   ending); each would read as `missing_steplog` on an unmutated run. They are counted as false positives, as
   registered, and each is listed with its reconcile fields so its cause can be read.
5. **Smoke.** At most 20 model calls, to prove the plumbing; not data, not committed.

## Amendment 2 — 2026-10-07, what the smoke showed (before the full run)

The 10-call smoke (4 runs, not data) showed that `thinking=False` does not reach `ollama_chat/` — the gateway
forwards it to OpenRouter only — so qwen3 reasons on every call, as it does wherever Chimera runs it locally.
The full run keeps it that way and records it. Nothing about the wire log or reconciliation depends on it;
it only sets the duration (73–235 s per run on the smoke). Prompts were 872–975 tokens, far under the
`num_ctx` guard. Every smoke run had exactly one wire record per step, as the clean class assumes.
