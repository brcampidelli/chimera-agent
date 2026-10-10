# Governance for deployers

Chimera provides mechanisms that can support your record-keeping and human-oversight processes. They do not make a deployment compliant: the controller remains responsible for the system, its configuration, records, people, and applicable law. The descriptions below are deliberately scoped to what the mechanisms establish; **they provide toward** the capabilities discussed in Articles 12 and 14 of the EU AI Act, not a determination of compliance. [EU AI Act, Articles 12 and 14](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)

## Traceable governance events

The governance audit log records events as JSONL entries in a hash chain; verification can identify changed or reordered chained entries. It can also compare the chain with a saved head anchor, which helps reveal truncation or rewriting since that anchor. This provides toward keeping automatically generated logs under Article 12. [Implementation: `chimera/governance/audit.py`](../chimera/governance/audit.py) · [Study results: audit and right-hand governance](../bench/right_hand_governance/RESULTS.md)

**Limits:** the chain detects some alterations; it does not prevent them. Deleting newest entries is not detectable from the remaining chain alone, and a person able to rewrite the whole log can recompute it. An anchor is useful only if the saved copy is protected independently; the implementation's ordinary run receipts are in the same home directory, not an independent trusted host. Legacy unchained entries cannot be validated as chained. [Implementation and documented threat model](../chimera/governance/audit.py)

## Human approval and durable questions

For configured approval flows, Chimera can pause for a person to answer a durable, identified question; silence times out as refusal, mismatched answers are discarded, and resolved outcomes are retained in history. This provides toward human oversight of covered decisions under Article 14, and toward records of those decisions under Article 12. [Implementation: `chimera/governance/pending.py`](../chimera/governance/pending.py) · [Study results: stale-consent probes](../bench/stale_consent/RESULTS.md) · [Study results: right-hand governance surfaces](../bench/right_hand_governance/RESULTS.md)

**Limits:** this is a mechanism, not proof that a human is competent, available, independent, or meaningfully exercising oversight. The deployer must configure the approval points, deliver questions to reachable people, and define escalation and review. On the synchronous durable path the effect runs within one poll interval of the answer, so a consent cannot age before it is used there (measured; no revalidation was built for it). That measurement does not cover a plan approved for a turn that then runs for minutes, or an editor's standing grant. [Implementation and scope](../chimera/governance/pending.py) · [Study findings and limits](../bench/stale_consent/RESULTS.md)

## Who decided, and whether review has become a rubber stamp

Each resolved approval is also written into the hash-chained audit log, with the whole action hashed (not the 200-character excerpt a card shows) and an `approver_kind`: `person` when someone answered, `system` when the timeout refused, `agent` when a connected editor granted on the user's behalf. The weekly review reports, for questions a person answered, how many were approved in under ten seconds — the habituation signal reviewers show as approval volume grows. [Implementation: `chimera/governance/pending.py`](../chimera/governance/pending.py) · [Weekly review: `chimera/scheduler/weekly_review.py`](../chimera/scheduler/weekly_review.py)

**Limits:** a fast approval is a signal to look at, not evidence of a careless one; the threshold is a fixed ten seconds. `approver_kind` says what kind of approver answered, not who the person was.

## The agent's own tools and the audit log

The agent's shell and code tools refuse commands that name the audit log, its route, its code or the CLI command that reads it. [Implementation: `chimera/core/audit_fence.py`](../chimera/core/audit_fence.py)

**Limits:** this narrows the ways in; it does not prevent them. A path the command builds at run time is not seen. Putting the data folder where the agent's tools cannot write (or running the agent in a sandbox without it mounted) is the structural answer, and it is the deployer's to make.

## An independent record of model calls: the wire log

A run's trace (`traces.jsonl`) is written by the run itself, when it ends. The wire log is a second record of the same model calls, written by a different component at a different time: the provider gateway appends one line for every exchange that returns, as it returns, to `CHIMERA_HOME/wire.jsonl`. `chimera audit reconcile` compares the two, so a step deleted from a trace, a step that was never sent to a provider, or a step whose recorded request or response was changed afterwards shows up as a disagreement. This provides toward keeping automatically generated logs under Article 12. [Implementation: `chimera/governance/reconcile.py`](../chimera/governance/reconcile.py) · [Study results: wire reconciliation](../bench/wire_reconcile/RESULTS.md)

**What a line holds:** a SHA-256 digest of the request and of the normalised response, the model, a timestamp, a random `wire_id`, and — written by the caller that made the call, never guessed from the request — the call's `kind` and the `run_id` of the agent run it belongs to. Never a message body, a tool argument, a credential or an authorization header. The kinds are `step` (the call a trace step records), `close` (the closing call when a run stops at its step limit, on the tool-loop breaker or on a browser handover), `empty_retry` (the re-ask after an empty final reply), `summary` (a compaction summary), `router` (the tool router), `tool` (a model call made by a tool, such as a judge) and `undeclared` (a call outside any agent run: cron jobs, bots, fusion, evaluation). The run's trace lists its own non-step calls (`side_calls`), so they are compared too.

**Turning it on.** It is OFF by default, and off it does no I/O. Set `CHIMERA_WIRE_LOG=true` in the environment of the process that makes the model calls. In the desktop app the same switch is in Settings › General › Governance and audit. The file is created with owner-only permissions (`0600`) on POSIX systems.

**Reconciling.** Reconcile one run at a time, by its id, soon after it ends:

```bash
chimera audit reconcile --run <run_id>
# a scheduled job's runs are traced to their own file:
chimera audit reconcile --run <run_id> --steplog "$CHIMERA_HOME/scheduler/cron_traces.jsonl"
```

The run id is on the run's trace line and on each of its wire records. The command prints JSON and exits with status 1 when the result is not clean. The discrepancy categories are `missing_steplog` (a `step` record no trace step carries), `missing_wire` (a step with no wire record), `altered` (same id, different digests), `duplicate_ids`, `kind_mismatch` and `run_mismatch` (a step witnessed only by a record of another kind or run), `unaccounted` (a non-step record its run's trace does not claim), `implausible` (a claimed call that does not fit the trace, such as a summary at a step that did not compact), `side_calls_without_wire`, and `missing_runs` (a run that promised a trace and left none — it raised, or its line was removed). Records outside any run, records of runs that write no trace, and records older than the oldest trace line still on disk are counted and reported, not compared. Records written before kinds existed are compared as steps, as before, and counted as `legacy_records`. `per_run` gives each run its own verdict. Without `--run` the whole file is compared; on a shared deployment that takes in every gateway user in the process and every run whose trace has rotated out, so per-run reconciliation is the intended use.

**Protecting the file.** The comparison is only as good as the wire log's independence. Make the file append-only for the process — on Linux, `chattr +a wire.jsonl` (as root; the process can then append but not rewrite or truncate it) — or ship its lines to storage the agent cannot write. Keep it out of reach of the agent's own tools, as for the audit log above. The wire log is never rotated or pruned by Chimera: set a retention period and archive or remove old files yourself, outside the process. A shared deployment should also review, once, that the lines it writes contain only the fields listed above.

**What it is not.** It observes; it never blocks or alters a call. It is not proof against a compromised deployment: the gateway and the harness run in the same process, so a process that lies when it makes the call, or anyone able to rewrite both files, defeats it — reconciliation establishes that two records agree, not that either is true. It compares digests the trace copied from the gateway, so an edit to a step's retained text that leaves the digests alone is not detected. With the response cache on (`CHIMERA_CACHE`) a cached call reaches no provider and writes no wire record.

**Measured, and the registered verdict.** On 100 long runs on local Ollama (`qwen3:4b`, with `gemma4:12b` as the fallback in one arm; compaction, summary calls, a forced mid-run model switch, streaming), 98 were analysable: 0 of 98 clean runs read as dirty (95% Wilson 0–3.8%), and deleted, fabricated and altered steps were each detected in 98 of 98. The verdict registered before the run is **"no pilot"**, because the rule required at least 100 analysable runs and two runs failed on a local timeout; the closing call, the weakest point, occurred in only 4 runs. Turning the option on remains a deployment decision taken with that evidence, not a recommendation of this project. [Study results, predictions and limits](../bench/wire_reconcile/RESULTS.md) · [Pre-registration and enablement rule](../bench/wire_reconcile/PREREGISTRATION.md)

**Known gap:** a model backend that makes several provider calls for one agent step — the fusion panel, or a cascade climbing tiers without tools — writes several `step` records for one trace step, and the extra ones still read as `missing_steplog`. Reconcile only runs whose backend makes one call per step until that is modelled.

## Use in a deployment process

Treat these features as inputs to a broader governance process: decide which actions require review, test delivery and timeout behavior in your environment, protect and independently retain records and anchors, verify logs regularly, and document who reviews exceptions. The implementation and studies describe bounded technical behavior, not an assessment of your particular system or legal duties. [Audit implementation and limits](../chimera/governance/audit.py) · [Approval implementation and limits](../chimera/governance/pending.py) · [EU AI Act, Articles 12 and 14](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)

This page is general information, not legal advice. Consult qualified counsel for an assessment of your obligations and deployment.
