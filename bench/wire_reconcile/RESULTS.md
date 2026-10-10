# Wire reconciliation — synthetic results

**Run:** deterministic fake-backend harness, seed `3061`; US$0; no provider/network calls. 30 trials per fault class and 30 clean controls; each run generated five gateway exchanges. The mutation was injected into the steplog after the gateway wrote its append-only wire log.

| Outcome | Detected / trials | Rate | Two-sided 95% Wilson interval |
|---|---:|---:|---:|
| Omission | 30 / 30 | 100.0% | 88.6%–100.0% |
| Fabrication | 30 / 30 | 100.0% | 88.6%–100.0% |
| Altered copy | 30 / 30 | 100.0% | 88.6%–100.0% |
| **Pooled faults** | **90 / 90** | **100.0%** | **95.9%–100.0%** |
| Clean-control false positives | 0 / 30 | 0.0% | 0.0%–11.4% |

These results meet the expected behavior for the registered synthetic mutations but **do not meet the preregistered rule for enabling the feature**: 30 trials per category is below the required 100, and the individual-fault lower confidence bounds are below 0.90. Keep `CHIMERA_WIRE_LOG` OFF unless a separate validation satisfies every preregistered condition. These data say nothing about real providers, production frequency, shared faults or gateway bypasses.

Reproduce from the repository root:

```cmd
uv run --extra dev --extra desktop python bench/wire_reconcile/run.py
```

## Local-Ollama replication (Amendment 1) — 2026-10-08

**Run:** `ollama_chat/qwen3:4b` on the local GPU, `CHIMERA_WIRE_LOG=true`, cache off, one fresh
`CHIMERA_HOME` per run, ten fixed file tasks × ten replicas, `max_steps=4` (protocol in
`PREREGISTRATION.md`, Amendment 1; command in `RUN.md`). US$0. 100 pristine runs, **224 model calls**
(76 runs made 2 calls, 24 made 3), every run stopped as `final`, **0 protocol failures**. Wall time
1.76 h (mean 63 s per run, 11–146 s). 79 of the 224 steps carried more than one tool call. No
compaction fired (it was off), no closing call was made (no run reached `max_steps`), and no fallback
was configured. Raw artifacts: `results/ollama/<run>/` (`home/wire.jsonl`, `home/traces.jsonl`,
`meta.json`, the workspace) and `results/ollama.json` (the `inject` output). The mutated copies
(`results/ollama-mutated/`) are not committed: `inject` regenerates them from the seed.

| Outcome (paired copies of the same 100 runs) | Detected / trials | Rate | Two-sided 95% Wilson interval |
|---|---:|---:|---:|
| Omission | 100 / 100 | 100.0% | 96.3%–100.0% |
| Fabrication | 100 / 100 | 100.0% | 96.3%–100.0% |
| Altered copy | 100 / 100 | 100.0% | 96.3%–100.0% |
| **Pooled faults** (300 paired copies, not independent) | **300 / 300** | **100.0%** | 98.7%–100.0% (nominal; the copies share runs) |
| Clean-copy false positives | 0 / 100 | 0.0% | 0.0%–3.7% |

The numbers meet the letter of the enablement rule for **this** shape of run. Read them for what they
are, though:

- **Detection is close to guaranteed by construction here.** Reconciliation compares exact digests
  keyed by `wire_id`. Deleting a step leaves a wire record with no partner, a fabricated step carries
  an id the wire log never issued, and an altered digest cannot equal the one written at the gateway.
  On a run of ~2 calls none of the three can hide. 100/100 shows the **implementation is correct** on
  real runs and real files; it does not show the problem is hard, and it says nothing about faults
  outside the three registered mutations (a text edit that leaves the digests alone is still not
  detected — see the limits below).
- **The clean 0/100 is the informative number, and it was earned on easy runs.** Every gateway call in
  these runs was a step. The calls that are *not* steps — a closing call after `max_steps`, a
  loop-breaker ending, a compaction summary — never happened, so this run could not have shown them as
  false positives. That is what the follow-up (Amendment 3: long runs with compaction, a forced model
  switch and streaming) exists to measure, and **no VPS pilot rests on this result alone**.
- One model (qwen3, one family), one machine, one Ollama version. Not a claim about other providers.

## Long runs (Amendments 3–5) — 2026-10-08/09

**Registered verdict: no pilot. Only 98 of the 100 runs the rule requires were analysable.** The rule
(Amendment 3, "Rule, fixed now", unchanged by Amendments 4 and 5) asks for 0 clean false positives over
**at least 100 analysable runs** and a Wilson lower bound ≥ 0.90 for each fault class's signature
detection. The second half passed and the false-positive count was 0, but two runs were protocol
failures, which the protocol reports and does not replace. The rule is read as written; it is not
amended after the fact to reach 100.

### Smoke on the unfixed reconciler (Amendment 4) — not data

4 runs, 34 model calls, ~28 min (worktree `d870e228`). **2 of the 4 untouched runs reconciled dirty**,
each for the reason Amendment 3 predicted by reading the code:

| smoke run | stopped | calls (steps) | non-step calls | clean copy |
|---|---|---:|---|---|
| `r00-t00-S-max4` (arm S at `max_steps=4`) | `max_steps` | 5 (4) | 1 closing call | **dirty**: 1 `missing_steplog`, the closing call |
| `r00-t01-M` (summarised compaction) | `final` | 13 (10) | 3 summary calls | **dirty**: 3 `missing_steplog`, one per summary call |
| `r00-t02-F` (forced switch) | `final` | 9 (9) | none | clean |
| `r00-t03-T` (streaming) | `final` | 7 (7) | none | clean |

Signature detection 4/4 per class. The run directories are not committed (as registered); the report is,
as `results/ollama-long-smoke-unfixed.json`, so the record of what the smoke showed is not a scratch file.

### Smoke on the fixed reconciler (Amendment 5) — not data

The same 4 runs on `fix/wire-log-call-kind` (`380a7f70`), ~26 min, in their own directory. The wire log
carried the caller-declared kind (`step` 34, `close` 1, `summary` 3), the trace claimed the four non-step
calls, and **all 4 untouched runs reconciled clean** (the added abort, "the fixed reconciler reads an
untouched smoke run as dirty", did not fire). Declared/inferred kind disagreements 0. Signature detection
4/4 per class. Report: `results/ollama-long-smoke-fixed.json`.

### The full run (Amendment 5, fixed reconciler)

100 runs of `chimera.core.agent.Agent.run`, `ollama_chat/qwen3:4b` (arm F: `ollama_chat/gemma4:12b` from
the 4th call on), local GPU, US$0, `max_steps=15`, compaction threshold 2,500 prompt tokens, four arms.
Wall time **10.1 h** (2026-10-08 14:11 → 2026-10-09 00:19; registered 8–14 h), 1,074 model calls. Raw
artifacts: `results/ollama-long/<run>/` (`home/wire.jsonl`, `home/traces.jsonl`, `meta.json`, the
workspace) and `results/ollama-long.json` (the `report` output; re-running `report` offline from the
committed runs reproduces it byte for byte). `results/ollama-long-mutated/` is not committed: `report`
rebuilds it from seed 30613.

| Outcome | k / n | Rate | Two-sided 95% Wilson interval |
|---|---:|---:|---:|
| **Clean false positives (primary)** | **0 / 98** | 0.0% | 0.0%–3.8% |
| arm S — structural compaction | 0 / 25 | 0.0% | 0.0%–13.3% |
| arm M — summarised compaction | 0 / 26 | 0.0% | 0.0%–12.9% |
| arm F — forced model switch | 0 / 23 | 0.0% | 0.0%–14.3% |
| arm T — streaming | 0 / 24 | 0.0% | 0.0%–13.8% |
| runs where compaction fired | 0 / 97 | 0.0% | 0.0%–3.8% |
| arm M runs with a summary call | 0 / 26 | 0.0% | 0.0%–12.9% |
| arm F runs where the switch happened | 0 / 23 | 0.0% | 0.0%–14.3% |
| **runs with a closing call** | **0 / 4** | 0.0% | **0.0%–49.0%** |
| Omission, signature detected | 98 / 98 | 100.0% | 96.2%–100.0% |
| Fabrication, signature detected | 98 / 98 | 100.0% | 96.2%–100.0% |
| Altered copy, signature detected | 98 / 98 | 100.0% | 96.2%–100.0% |
| **Rule: ≥ 100 analysable runs** | **98** | — | **fails** |

Descriptive: analysable runs 98, protocol failures 2; model calls in analysable runs 1,050 (median 10 per
run, range 2–19 — the 2-call run, `r01-t07-S`, answered without following the chain and is the one run
where compaction never fired); **225 compactions**, **59 summary calls**, **8 closing calls**, **23
switches** (209 primary attempts refused by the injected outage, none of which left a wire record; no
qwen record after any switch); stops `final` 94, `max_steps` 4, `context_stuck` 0; steps with more than
one tool call 1 / 983; step/wire model-label mismatches 0; files in `home/` the runner did not expect 0;
caller-declared kinds `step` 983, `summary` 59, `close` 8, and **declared/inferred kind disagreements
0**. No discrepancy of any category on any clean copy. The "any discrepancy" reading of each fault class
is also 98/98; it decides nothing, and on this run it could not differ from the signature reading because
every clean copy was clean.

**The two protocol failures.** `r01-t05-F` and `r09-t05-F` — the same task (t05) in the same arm (F) —
each raised `APIConnectionError: … Timeout: Connection timed out after 600.0 seconds` on their 12th
model call, after the switch, so the call that never answered was a `gemma4:12b` fallback call. The
11 calls before it are in their wire logs; with no answer there is nothing to tap, and with an exception
out of `Agent.run` there is no trace line. The likely cause is the machine, not the reconciler: qwen3:4b
and gemma4:12b both loaded on an 8 GB GPU, so the larger model ran partly off the GPU and one long-context
call outlived LiteLLM's 600 s timeout. Registered treatment (Amendment 3 §8): reported, counted, not
replaced. Under the fixed reconciler, such a run's records would read as `missing_runs` — a run that
promised a trace and left none — which is the right reading for a run that raised.

**The weak spot: the closing call was exercised in 4 runs.** All four are arm F (gemma4:12b answering),
all four stopped at `max_steps`, and each made 2 closing calls (an empty first closing reply, asked once
more). So "closing calls are no longer false positives" rests on 0/4 (Wilson upper 49%), on one model, on
one stop reason. The tool-loop breaker and browser-handover closes, the `empty_retry` re-ask, and the
`router` and `tool` kinds never occurred in this run (0 records each); they are modelled and pinned by
offline tests (`tests/test_wire_call_kind.py`), not measured.

### Predictions, marked

Amendment 3 (made for the unfixed reconciler; 3, 4 and 7 were replaced by Amendment 5 for the full run):

| # | Prediction | Outcome |
|---|---|---|
| 1 | Compaction fires in ≥ 90 runs, 2–4 times in a typical run; no `context_stuck` | **held**: 97 of 98, median 2 (range 0–5), 0 `context_stuck` |
| 2 | Structural compaction causes 0 false positives | **held**: 0 in arms S, F, T |
| 3 | (unfixed) every arm-M run with a summary call is a false positive, one `missing_steplog` per summary call | **held in the smoke** (3 summary calls → 3 `missing_steplog`, attributed `summary`); the full run was not made on the unfixed code (Amendment 4) |
| 4 | (unfixed) closing calls are a false-positive cause; 5–25% of runs end on `max_steps` or the loop breaker | cause **held in the smoke** (1 closing call → 1 `missing_steplog`, attributed `close`); frequency **refuted, narrowly**: 4 of 98 (4.1%) on the fixed run, all on gemma4:12b |
| 5 | Arm F: switch in ≥ 22 of 25 runs, 0 FP, refused attempts leave no wire record; arm T: 0 FP | **held**: switch in 23 of 23 analysable runs (the 2 lost runs had switched too), 0 FP, 209 refusals with no record; T 0/24 |
| 6 | No `missing_wire`, `altered` or `duplicate_ids` on a clean copy | **held** |
| 7 | (unfixed) clean FP about 30–45/100, rule fails | **direction held in the smoke** (2 of 4 dirty); not measured at scale, by the owner's decision (Amendment 4) |
| 8 | Signature detection 100/100 per class, any-discrepancy also 100/100 | **held** on the analysable runs: 98/98 each, both readings |

Amendment 4 (smoke, unfixed): the M run makes ≥ 1 summary call and reads dirty once per call — **held**
(3); the `max4` run ends `max_steps` with 1–2 closing calls and reads dirty once per call — **held** (1);
the F and T runs are clean — **held**; ~35–45 calls, 20–30 min — 34 calls (one under), ~28 min.

Amendment 5 (fixed full run): (3') arm-M runs with a summary call are not false positives — **held**,
0/26; (4') runs ending on a closing call are not false positives — **held, on 4 runs** (0/4, Wilson upper
49%); (7') overall clean FP 0/100, so part (1) passes and the verdict is "supports an observe-only VPS
pilot" — **0 false positives held, the verdict is refuted**: the count was 0/98 and part (1) of the rule
needs 100 analysable runs. The prediction priced in no protocol failure. (9) declared/inferred kind
disagreements 0 — **held**.

### What this cannot show

- **The closing call, beyond 4 runs on one model and one stop reason** (above).
- **Kinds that never occurred:** `empty_retry`, `router`, `tool`, closes on the tool-loop breaker or a
  handover. Modelled and tested offline only.
- **A shared wire log.** One `CHIMERA_HOME` per run, so the VPS shape — cron jobs, bots, the fusion
  panel and judges writing to the same `wire.jsonl` (`outside_runs`, counted and not compared), and the
  trace rotating while the wire log does not (`before_trace_window`) — was not exercised.
- **The known gap:** a backend that makes several gateway calls for one agent step (the fusion panel,
  the cascade's tool-free climb) leaves `step` records with no `StepRecord`, which still read as
  `missing_steplog`. No arm used one.
- **The response cache** was off; a cached summary call writes neither a wire record nor a step.
- **Detection is still close to guaranteed by construction** (exact digests keyed by `wire_id`; see the
  Amendment-1 section), and an edit to retained text that leaves the digests alone is still not detected.
- **Both writers compromised.** The kind is written by the harness at call time; a harness that lies when
  it makes the call, or a rewritten wire file, is outside what reconciliation can establish.
- Two models (qwen3:4b, gemma4:12b), one machine, local Ollama. Not a claim about OpenRouter routes.

### The lesson

**Register a margin of extra runs to absorb protocol failures.** The rule said "at least 100 analysable
runs" and the plan made exactly 100, so any failure of the machine — not of the thing measured — was
enough to fail it. Two timeouts on an 8 GB GPU did exactly that, after ten GPU hours. The fix belongs in
the registration, before the run: plan, say, 110 runs in a fixed order and read the first 100 analysable
ones (or all of them), with the stopping rule written down; failures stay reported either way. Choosing
the margin afterwards would be the post-hoc amendment the owner declined here.

### The owner's decision, separate from the rule

On 2026-10-09 the owner decided to run an **observe-only pilot on the VPS** after this change merges,
reconciling each run by its id. That is **his own call, made with this evidence in hand — not the rule's
verdict**, which remains "no pilot (98 of 100 analysable)". The pilot's readings will be reported as a
pilot's, and nothing above is re-scored by it. The option stays OFF by default for everyone else;
`docs/governance-for-deployers.md` describes what turning it on involves.

## Limits found in review (2026-10-06)

- **"Altered copy" here means an edited digest field, not an edited text.** The mutation replaces the
  `response_digest` the step copied from the gateway. Reconciliation compares those copied digests;
  it does not recompute them from the step's retained (clipped) `content`, so an edit to the text
  that leaves the digests alone is **not** detected — pinned by
  `test_an_edit_to_retained_content_is_not_detected_documented_limit`. The 30/30 above is about the
  registered mutation and says nothing about content edits.
- **A cache hit makes no provider call**, so with `CHIMERA_CACHE` on its step carries no `wire_id`
  and is reported under `missing_wire`. Read such entries against the cache before calling them
  fabricated.
- The first implementation tapped only the blocking paths; the streaming path — the one the coding
  turn uses by default — was added in review. The synthetic run above drives `complete` only and is
  unchanged by that fix (re-run: identical counts).
