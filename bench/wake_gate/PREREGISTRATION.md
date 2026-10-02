# The wake gate on the cron daemon — pre-registration

Study 27, phase 1 (`bench/PLAN-study27-jev-ecosystem.md` §2). Written 2026-09-28, **before any
call**. Local only (Ollama `qwen3:4b`), US$ 0.

## Question

A cron dispatch that resumes the agent with its whole context often ends in "nothing changed, back
to sleep", and the turn's tokens are spent anyway. The ecosystem's wakegate puts one typed decision
in front of the resume. The question here is whether the mechanism ranks *our* dispatches, before
anything is allowed to sleep:

> On dispatches of our own cron jobs, does a local decision-first model, asked a three-way wake
> question over the job, its last result and the event, rank the wakeups worth a full turn above
> the ones that are not?

Nothing sleeps in this bench. The gate ships **shadow**: every dispatch still runs; the verdict is
recorded beside the dispatch line in `cron_results.jsonl` and read later.

## Why this is allowed to (eventually) suppress a dispatch

A skipped cron turn loses no verification, no review and no human card — the job runs again on its
next tick. The gate decides **spend**, not scrutiny, so I1 is not at stake in the direction the B4
router was. The danger case — sleeping through something that matters — is bounded in code, not in
the model, and those bounds are asserted by tests rather than measured by this bench:

* an event raised by the user's message is **never asked** — it always wakes;
* any error, timeout or unparseable answer **wakes** (`on_no_signal` = wake, the opposite of the
  band's quiet pass — here the safe direction is the expensive one);
* a gate that fires `not_yet` on `skip_limit` consecutive dispatches **wakes** (`max_skips`, a
  setting, default 10 — their table, adopted verbatim in direction);
* only a confident answer — the choice is `wake` **and** its probability is below the skip floor
  (0.2, swept in this bench but never shipped without the sweep beside it) — suppresses one tick.

## The instrument (fixed now, byte for byte)

**State** — a dict, built by `build_wake_state(job, last_result, event, skipped)`:

| field | content | cap |
|---|---|---|
| `job` | name, schedule, the action's first 600 characters | 600 chars |
| `last_result` | the `CronResult` summary: status line, the answer's first 600 characters | 600 chars |
| `event` | what fired this dispatch: `"timer"`, `"webhook: <name>"`, `"message: <from>"` | name only, no body |
| `skipped` | consecutive skips so far (int) | — |

No message body, no workspace path, no output beyond the 600-character summaries. The redaction
page (study 27 phase 3) is written before this spec ever reaches a hosted backend; this bench runs
local only.

**Question** — a Choice, `key = "wake"`, instructions:

> A scheduled job's agent was asleep. Something fired. Decide whether this is worth waking the agent
> for a full turn, or whether it should keep sleeping. "wake" means the event plausibly changes
> what the agent is waiting on or produces something a person asked for. "not_yet" means the event
> is routine progress the next scheduled tick would cover anyway. "unrelated" means the event has
> nothing to do with what the job is for.

criteria (neutral ids, meaning in the criteria — I3):

| option | criterion |
|---|---|
| `wake` | Resume the agent now: the event merits a full turn this tick. |
| `not_yet` | Stay asleep this tick; the next scheduled tick covers it. |
| `unrelated` | The event does not concern this job at all. |

The three-way wording is wakegate's dev-set winner, picked from three candidates on a separate set
of theirs — recorded here as provenance, not as our measurement. It lints clean
(`chimera/decisions/lint.py`); the runner aborts before any call if it does not.

**Backend** — `LocalLogprobBackend`, `qwen3:4b`, decision-first, `temperature 0`, read in isolation
(one state, one question, no battery). `num_ctx = 16384` on the request; every response's
`prompt_eval_count` must be below it (the spot check's §2ad rule: Ollama truncates silently).

## Items

Scenarios written **by hand, and labelled before any run**, over the owner's real cron jobs in
`<home>/scheduler/jobs.json` plus synthetic events. Registration rules:

* ≥ **30 scenarios**, split ≥ 10 wake / ≥ 10 not-yet / ≥ 5 unrelated, plus the **2 boundary
  families** the wakegate table treats as fixed rules and this bench measures only to confirm the
  rules behave: a user-raised event (must always wake — asserted, not scored as a model reading,
  since the gate never asks) and an error event (same).
* Each scenario names: the job, the event, the expected label, and **one sentence of why**.
* The corpus is committed with the bench (`items.jsonl`), before the first call, and the labeler
  (the author of this file) is not the model. The known weakness — the same person wrote the
  scenarios and the question — is wakegate's own stated limitation and is carried here rather than
  hidden: this bench measures *ranking*, not ground truth in the world.
* One repetition. Greedy decoding on this machine has been 55/55 identical across calls
  (`bench/decisions_v2/RESULTS.md`); a repeat arm costs nothing and confirms it — any flip is
  reported, not averaged away.

## Arms

* **W** — the wake question, raw choice and its probability, the gate's policy read in code (the
  registered table above).
* **T** — timer-only baseline: the rule "always wake on a timer, never suppress" (what the daemon
  does today). This is the control the savings number is read against; it makes no calls.

## Metrics

* **Primary:** on the scored scenarios (excluding the rule-boundary families): the count of
  **false skips** — scenarios labelled wake where W's policy suppresses. Target **0**; this is
  wakegate's headline number and the pre-registered kill line.
* **Secondary:** wakeups correctly suppressed (not-yet + unrelated where W slept); the suppression
  rate on timers (the spend story — the share of a day's ticks that would have been skipped);
  latency p50/p95 per call; tokens per call; W's cost per 1,000 ticks at the shipped prices,
  printed rather than estimated.
* **The sweep, reported not shipped:** the skip floor at 0.1 / 0.15 / 0.2 / 0.3 / 0.5 — false skips
  and suppressions at each. The shipped floor carries the sweep beside it in the results, the way
  the ecosystem's READMEs do; nothing is tuned to the corpus and then presented as chosen a priori.

## Decision rule (fixed now)

* **Ships shadow** — false skips = 0 **and** W's suppression rate on not-yet/unrelated ≥ 50%: the
  spec stays in the registry in `Mode.SHADOW`, a shadow verdict lands in `cron_results.jsonl`
  beside every dispatch, and the drift alarms of phase 3 watch it. A quiet shadow log proves
  nothing; the shadow period is bounded by the **clock** (one week of real dispatches read in the
  open), not by a count.
* **Null** — false skips = 0 but suppression < 50%: no shadow verdict is written; the gate exists
  as a spec and a corpus, and the results say the mechanism did not separate our traffic. The
  daemon is unchanged.
* **Harm** — any false skip: the gate does not ship at all, the corpus and every answer are
  published, and the miss is named (which job, which event, what the state lacked).

Enforce — the mode that actually skips a tick — is **not decided by this bench**. It needs its own
gate: one week of shadow verdicts beside real dispatch outcomes, read against the jobs' own results
(a suppressed tick is correct when the next tick produced nothing new), pre-registered separately.

## Prediction (written before running)

Weak but real ranking, near the ecosystem's own numbers: AUROC on wake vs not-yet/unrelated between
0.70 and 0.85 (their smoke test was 21/21, n too small to be evidence, and their two close calls
were both in the unsure band); the unsure band carrying 10–30% of timers; false skips 0 at the 0.2
floor **only because** the floor is conservative and the local model leans toward `not_yet` rather
than `unrelated` — the failure I actually expect is over-suppression on the `unrelated` option,
which is the harmless direction for spend but shows up in the suppression-rate column.

## What this cannot show (§2q)

* Whether suppressing ticks is *worth it* in dollars on real traffic — that needs the shadow week
  against real dispatch outcomes, and is explicitly out of this registration.
* Anything about a hosted backend (Jev, ~250 ms p50, their measured latency) — this is the local
  4B only; the hosted arm is a different instrument with a different map, gated separately.
* Ground truth beyond the author's labels — one labeller, scenario author = question author; the
  direction of that bias (toward clean, obvious scenarios) is stated, not corrected.
* Any job type not in the owner's current cron set; new job kinds need their own scenarios.
