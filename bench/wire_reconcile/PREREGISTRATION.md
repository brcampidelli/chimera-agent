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

## Amendment 3 — 2026-10-08, long runs: do non-step gateway calls make clean runs read as faults? (written before any code or model call)

Numbered 3 because Amendment 2 (the smoke findings) already exists; the owner's request called it
"Amendment 2". Nothing above is changed. The Amendment-1 run (RESULTS.md, 2026-10-08) gave 0/100 clean
false positives and 100/100 per fault class on runs of 2–3 calls, where **every gateway call was a
step**. This amendment asks the question that run could not answer: on LONG runs, does the steplog
legitimately diverge from the wire log, so that an unmutated run reconciles as dirty?

### What the code does today (read before registering)

The reconciler (`chimera/governance/reconcile.py`, behind `chimera audit reconcile`) keys both files by
`wire_id` and reports `missing_steplog` (a wire record no step carries), `missing_wire` (a step with no
or an unknown `wire_id`), `altered` (same id, different digests) and `duplicate_ids`. It models nothing
else: any gateway call that is not recorded as a step is a discrepancy. For each candidate cause:

| candidate cause | what exists in the code | predicted effect on a clean run |
|---|---|---|
| **Closing call** | `Agent._close` (end at `max_steps`, tool-loop breaker, browser handover) calls the backend through `_step` and **never adds a `StepRecord`**; an empty closing reply is asked once more (a second call). | each closing call is a wire record with no step → `missing_steplog` → **false positive** |
| **Context compaction, structural** (`context_budget` set, `summarise_compaction=False`) | `compact()` rewrites the *message list* after the step was recorded; the `StepLog` is a separate object and is never rewritten; no model call. | none |
| **Context compaction, summarised** (`summarise_compaction=True`) | `rule_summariser` calls `backend.complete` (temperature 0, no tools) on the agent's own backend — through the gateway, so it is tapped — and records no step. | each summary call → `missing_steplog` → **false positive** |
| **Retries / fallback chain** | `LLMGateway.complete` walks the primary, then `CHIMERA_FALLBACK_MODELS`; an attempt that raises is never tapped (the tap runs only on a returned response); the answering candidate is tapped once and the step copies its digests. The stream path falls back to `complete` only before the first delta. | none: one wire record per answered call, and the reconciler does not compare the `model` field |
| **Mid-run model switch** | the agent switches by itself only on `escalate_on_tool_loop` (off); the gateway "switches" when the primary fails and a fallback answers. | none (as above) |
| **Response cache** | `CompletionCache` serves only tool-free, temperature-0 calls. Agent steps always send tools at temperature 0.2; the closing call is tool-free at 0.2; only the summariser call (temperature 0, no tools) is cacheable, and a hit there writes **neither** a wire record nor a step. | none on a step; a hit can only make a summary call invisible to both files. **Not exercised** (cache off, as in Amendment 1); pinned by an offline test instead |
| **Streaming** | `stream_complete` is tapped since the review fix (RESULTS.md, limits). | none |
| **Parallel tool calls** | one response carrying several tool calls is one step and one wire record. Amendment 1 already had 79 such steps out of 224, with 0 false positives. | none |
| **Tool router / cascade / tool-loop escalation** | each would be a non-step call or a model switch; all off by default and off here. | not exercised |

Out of scope here, and recorded because each one breaks a whole-file reconcile **on the VPS** whatever
this run shows: (a) `wire.jsonl` lives in `CHIMERA_HOME` and every gateway user in the process writes
to it (cron jobs, fusion panel, judges, bots), while `traces.jsonl` holds only `Agent` runs that set
`trace_path`; (b) the trace file rotates at its size cap (`steplog._rotate_if_large`) and the wire log
does not; (c) a run that raises after some calls leaves wire records and no trace line. This bench uses
one `CHIMERA_HOME` per run, so none of them can appear in it. A pilot needs run-scoped reconciliation
whatever this run shows.

### Design

1. **Runs.** 100 pristine runs of `chimera.core.agent.Agent.run` on `ollama_chat/qwen3:4b`, set up as in
   Amendment 1 §1 (one fresh `CHIMERA_HOME` per run, `CHIMERA_WIRE_LOG=true`, `CHIMERA_CACHE` off,
   `trace_path=<home>/traces.jsonl`, `num_ctx=16384`, `temperature=0.2`, `thinking=False` asked — it does
   not reach `ollama_chat/`, Amendment 2) except: `max_steps=15`, `auto_continue=False` (the library
   default, which is what a cron job runs; `attended()` surfaces turn it on and so never make the
   `max_steps` closing call, but they keep the tool-loop one), and the tasks.
2. **Tasks that force 8–15 calls.** Ten fixed tasks, each in a workspace seeded deterministically with a
   **chain** of 6–8 files (each ~1,400 characters: a value, the name of the next file, and filler) plus
   decoy files. The task says to read one file at a time, following each file's "Next file" line, and
   then write a result file and read it back. The chain cannot be read in parallel without guessing
   names, so a run needs about chain length + 3 calls (9–11). Tools as Amendment 1: `read_file`,
   `write_file`, `edit_file`, `list_dir`, rooted at the per-run workspace.
3. **Compaction forced on, in every run.** `context_budget` is not a threshold but a fraction of the
   model's window, and the window of `ollama_chat/qwen3:4b` comes from the catalogue or the cached index,
   which can differ between machines. So the runner computes the fraction at run time so that the agent's
   own `ContextBudget.threshold` is **2,500 prompt tokens** (asserted, ±1), with `keep_recent=4`. Not the
   lowest the config *accepts* — any fraction above 0 is accepted, and a threshold under the system prompt
   (~900 tokens) would end every run at once as `context_stuck` — but the lowest that stays above the
   expected post-compaction prompt (~1,900: system, restore note, four kept messages), so compaction fires
   repeatedly (predicted 2–4 times per run) and the run keeps going. A run that still ends as
   `context_stuck` is kept and reported under that reason. Compaction is decided on the prompt count the
   provider returns; if Ollama under-reports it, compaction will not fire, which the smoke (§7) catches.
4. **Four arms**, assigned before the run as `arm = (task + replica) mod 4` over the ten tasks × ten
   replicas, in a fixed order that interleaves them (S 25, M 26, F 25, T 24 runs):
   - **S — structural compaction**, blocking `complete`.
   - **M — summarised compaction**: `summarise_compaction=True`; one extra call per compaction.
   - **F — forced mid-run model switch**: `CHIMERA_FALLBACK_MODELS=ollama_chat/gemma4:12b` (already pulled
     locally; a different family; nothing is pulled), and from the run's **4th** model call onward an
     in-process wrapper around `litellm.completion` raises a `ServiceUnavailableError` for the primary
     before any request leaves the process. The gateway's fallback chain is real; the outage is
     simulated. Every later call is answered by gemma4:12b. Structural compaction.
   - **T — streaming**: the run passes a no-op `on_token`, so every step goes through `stream_complete`.
     Structural compaction.
5. **Fault trials on the long runs**, as Amendment 1 §2: each pristine run is copied four times (clean,
   omission, fabrication, altered copy), only the copy's steplog is mutated, mutation choices from
   `random.Random(30613)` in run order. Classes are paired on the same runs.
6. **Detection, restated for runs whose clean copy may already be dirty.** Amendment 1 read "detected"
   as "any discrepancy", which coincided with the fault there because every clean copy was clean. Here a
   closing or summary call would make every copy of that run dirty and score the fault as found when it
   was not. So the **registered** detection is the fault's own signature: omission — the deleted step's
   `wire_id` is in `missing_steplog`; fabrication — the inserted `wire_id` is in `missing_wire`; altered
   copy — the target's `wire_id` is in `altered`. The "any discrepancy" reading is printed beside it and
   decides nothing.
7. **Smoke** (not data, not committed, separate directory): 2 runs, one of arm F and one of arm T (the
   two arms with new plumbing). The queue **aborts** (non-zero exit) before the full run if compaction
   fired in neither, or if either smoke run is a protocol failure.
8. **Protocol failures** (an exception out of `Agent.run`, zero steps, or no wire record) are reported
   with their count and are not replaced. The rule below needs 100 analysable clean trials, so a run with
   any protocol failure cannot satisfy it; that is reported, not repaired.

### Instrumentation guard (§2r: an intervention reports how much it acted)

Every run's `meta.json` reports: model calls made, and each call's kind (`step` — sent with tools;
`summary` — the summariser's system prompt; `close` — anything else), its `wire_id` and the model that
answered; steps recorded; compactions fired (steps marked `compacted`); primary attempts refused by the
injected outage; switches (changes of answering model across the run's calls); and the stop reason.
Each false positive is attributed by `wire_id` to the call kind that produced it. Readings about a
mechanism are made only over the runs where it acted: a run in which compaction never fired is reported
separately and not counted as having tested compaction; an arm-F run with no switch (it ended before its
4th call) is reported separately and not counted as having tested the switch; an arm-M run with no
summary call likewise. All runs still count toward the clean false-positive rate.

### Registered outcomes

- **Primary:** clean false-positive rate over all analysable runs, `k/n` with the two-sided 95% Wilson
  interval; every false positive listed with its run, arm, stop reason and the cause of each discrepancy.
- Per cause: discrepancies and affected runs, by call kind (`close`, `summary`, a step without a wire
  id, digest mismatch, duplicate).
- Per arm: clean false positives `k/n` with Wilson (descriptive; no between-arm test).
- Per fault class: signature detection `k/n` with Wilson, and the any-discrepancy reading beside it.
- Descriptive: calls per run (median, range), compactions per run, switches, stop reasons, the share of
  steps with more than one tool call, and how often a step's `model` differs from its wire record's.

### Rule, fixed now

The result **supports a VPS observe-only pilot** only if **both**: (1) clean false positives are 0/100
(Wilson upper ≤ 3.7%) over at least 100 analysable runs; and (2) each fault class's signature detection
has a Wilson lower bound ≥ 0.90. Anything else: no pilot. Any false positive is reported with its cause,
and if the cause is **legitimate behaviour** of the harness (a closing call, a summary call, a fallback,
a cache hit), the reconciliation must model it — record the call, or teach the reconciler to expect it —
and be re-measured under a new amendment before any pilot. A false positive is never explained away,
and the rule is not loosened afterwards. Even a pass leaves the out-of-scope causes above standing: the
pilot would need run-scoped reconciliation first.

### Predictions (written so they can be wrong)

1. Compaction fires in ≥ 90 of 100 runs, 2–4 times in a typical run; no run ends `context_stuck`.
2. **Structural compaction causes 0 false positives**: among arm S, F and T runs, no discrepancy is
   attributed to compaction.
3. **Arm M: every run with a summary call is a false positive**, with exactly one `missing_steplog` per
   summary call — at least 23 of its 26 runs.
4. **Closing calls are a false-positive cause**: 5–25% of runs end at `max_steps` or on the tool-loop
   breaker, and every one of them is a false positive (one or two `missing_steplog`).
5. Arm F: the switch fires in at least 22 of 25 runs and causes 0 false positives; the refused primary
   attempts leave no wire record. Arm T: streaming causes 0 false positives.
6. No `missing_wire`, `altered` or `duplicate_ids` on any clean copy.
7. **Overall: clean false positives about 30–45/100, so the rule FAILS** — the predicted verdict is "no
   pilot; the reconciler must model summary and closing calls first".
8. Signature detection 100/100 in each fault class (by construction, as in Amendment 1), and the
   any-discrepancy reading also 100/100 — the gap between the two readings is the reason §6 exists.

### PROTOCOL §11–§14

- **§11 (interval):** one proportion at a time, two-sided 95% Wilson (`chimera.eval.proportions.wilson`)
  for the clean rate, each fault class, each arm and each mechanism subset. No bootstrap. The pooled fault
  rate is not used (the classes are paired copies of the same runs, not independent trials), and no two
  arms are compared.
- **§12 (margin):** no equivalence or non-inferiority claim. "0/100" is the enablement threshold fixed in
  the original registration, read as a bound (Wilson upper ≤ 3.7%), not as "no difference" between short
  and long runs or between arms.
- **§13 (controls):** the control this number needs is the **clean copy** — the null mutation, run on
  every run — and it is the primary outcome. *Trivial-agent arm:* not applicable; the bench scores the
  reconciler, not the agent's task success, and an agent that does nothing makes a 1-call run, which is
  Amendment 1's case. *Random arm at matched cost:* no selection mechanism. *Grader-hijack probe:* the
  model's tools are rooted at the per-run `workspace/`, and the two files the grader reads live in the
  sibling `home/`; after each run the runner lists `home/` and records any file it did not expect.
  *Arguments not tool names, detection probe beside a judge, placebo arms, rule-withdrawn arm:* not
  applicable — no attack, no judge, nothing added to a prompt, no instruction-following score.
- **§14 (model scope):** nothing is removed or defaulted off; the verdict is about turning an opt-in ON
  for an observe-only pilot. Measured on `qwen3:4b` locally (and `gemma4:12b` for the switched calls of
  arm F). The VPS runs other models over OpenRouter; the mechanisms measured here (closing calls, summary
  calls, fallback, streaming) belong to the harness and not the model, but how often a run makes a
  closing call does depend on the model, and the result is named with the model it was measured on.

### Cost and duration

US$0, local GPU only. Expected ~1,250 model calls (100 runs × ~11 steps, plus ~70 summary and ~15
closing calls). At Amendment 1's measured ~28 s per call with qwen3 thinking, about **10 h**; arm F's
gemma4:12b calls and the model swaps it causes are unmeasured, so **8–14 h** is the registered
expectation. The run waits for the serial GPU queue (S30-51) and never runs beside it.
