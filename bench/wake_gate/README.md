# bench/wake_gate — the cron wake gate, before it exists

`PREREGISTRATION.md` is the registration; `run.py` is the runner. **`items.jsonl` is not written
yet, on purpose**: the corpus must be labelled before the first call, and this README is the
checklist that keeps that true.

## What must exist before `run.py` is executed once

`items.jsonl`, one scenario per line:

```json
{"id": "price-watch-01",
 "label": "wake",                       // wake | not_yet | unrelated | rule:user_event | rule:error_event
 "why": "one sentence — the reason the label is what it is, written before any answer",
 "job": {"name": "price-watch", "schedule": "*/30 * * * *", "action": "check the 1000XM6 price …"},
 "last_result": "the job's most recent dispatch summary, 600 chars max",
 "event": {"kind": "timer"},            // or {"kind": "webhook", "name": "stripe"} / {"kind": "message", "from": "dana@acme"}
 "skipped": 0}
```

Registration rules (from the pre-registration, binding):

1. ≥ 30 scenarios: ≥ 10 labelled `wake`, ≥ 10 across `not_yet`/`unrelated`, ≥ 5 `unrelated`.
2. ≥ 2 `rule:` rows — a user-raised event and an error event — which the runner **asserts, never
   scores**: the gate must not ask the model about those at all.
3. Labels and `why` written **before** any call, committed with this file. The author of the
   scenarios is the author of the question; that bias is declared in the pre-registration's §2q and
   is not corrected here.
4. `label: wake` + `why` must name the thing that changed; `not_yet` must name the next tick that
   covers it. A `wake` row whose why names no change is not a scenario.

## Running

```bash
python -m bench.wake_gate.run          # local Ollama, qwen3:4b, US$ 0
```

Writes `results/wake_gate.jsonl` (one row per scenario) and `results/summary.json` (the registered
metrics, the sweep over the skip floor, and the decision-rule outcome read out of the numbers —
never chosen after reading them).

## What the runner refuses to do

* Run without a registered corpus (the exit above names exactly what is missing).
* Run with a question that does not lint (`chimera/decisions/lint.py`, checked first).
* Accept a prompt above `num_ctx = 16384` — Ollama truncates silently, so every response's
  `prompt_eval_count` is checked and a truncation aborts the run (the §2ad rule).
* Ask the model about a `rule:` row — those are assertions about the code's behaviour.

## What this bench does not decide

Enforce — actually skipping a tick — is a separate pre-registration, gated on one week of shadow
verdicts beside real dispatch outcomes. Nothing here sleeps, skips or saves anything.
