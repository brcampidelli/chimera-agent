# Stale consent in durable approvals — pre-registration

Study 31, G31-03. Written 2026-10-05, **before any probe ran**. No model call anywhere, US$ 0:
every scenario drives the real `chimera/governance/pending.ask_durably` with an injected clock and
an answerer thread.

## Question

The study's critique, in the plan's own words: "aprovações duráveis que esperam minutos ou horas
podem executar um efeito diferente do mostrado (stale consent)". EffectGuard measured the cost of
that family at 7.8% of effects under approval-by-call-or-argument. Before building any
revalidation of the effect, measure whether our mechanism can produce it at all, and where.

Reading the mechanism first changed the question, and the change is registered, not hidden: in the
**synchronous durable path** the wait ends the moment the answer file appears, and the effect (the
tool call the approver gated) runs immediately after the return. The consent's age at effect time
is therefore bounded by one poll interval **by construction**. The surfaces where a consent can
genuinely be old when the effect runs are two, and both are declared rather than accidental:

* **The plan gate** (`chimera/api/plan_gate.py`): a person approves a PLAN; the turn then runs for
  minutes, and every dangerous action inside it is supposed to meet its own per-action approval.
  The gate's docstring claims "it does NOT pre-approve anything" — a claim about coverage, which is
  exactly what 2608.27443 measured failing elsewhere (user-written policies protected 20.1 pp less
  than per-action approval).
* **The editor's standing grants** (`allow_always` chosen by a person in the ACP editor): consent
  once, effects indefinitely after. That is the editor's contract, not ours to revalidate; named
  here as out of scope, not measured.

## What is measured (the four probes)

All four drive the real `ask_durably`. The fake clock (`clock=`, `sleep=`) makes the waits
deterministic; one scenario runs on the real clock because a fake clock cannot catch a scheduling
surprise.

| # | scenario | the number it produces |
|---|---|---|
| S1 | answer lands at t+Δ for Δ in a fixed grid, wait 300, poll 2 and 0.5 | the consent-to-effect gap (fake-clock ticks from the answer's `answered_at` to the ask's return); also that the record's `seconds_to_answer` equals Δ exactly (the person's clock column is the one G31-04 reads) |
| S2 | answers placed across the deadline: `limite − k·poll` for k in {2, 1, 0.5, 0.25} and after it | boundary violations: an outcome of `timeout` while an answer existed at the last look (must be 0); and the chat-code bound — `answer_with_code` at `expires_at − ε` applies, at `expires_at + ε` returns `expired` (the design that keeps a chat acknowledgement out of the last interval) |
| S3 | the orphan: the answer lands, the waiter is abandoned before its return is consumed | the record must already say `approved` (it is written before the return), and the effect — gated on the return value — must have run 0 times; the production join this stands for is `history.jsonl` says approved while `runs.jsonl` shows the turn stopped |
| S4 | real time, 10 runs: wait 5 s, poll 0.2, answer at ~1 s | the REAL wall-clock gap between the answer file's mtime and the ask's return — the probe that could actually surprise (thread scheduling, filesystem latency) |

**The plan-gate drift number** (approved plan vs the actions the turn took, joined by the
`run_id` the gate already puts in `facts`) is specified here and **not measured**: this machine's
home has no recorded plan-gated turn (its `approvals/` is empty — checked 2026-10-05). A number
over zero turns would be the plausible-zero the protocol exists to kill. The join is committed in
`run.py` (`plan_gate_drift_spec`), the weekly review and G31-01's chain accumulate the rows, and
the first recorded plan-gated turn is the trigger to run it. Until then the claim "the per-action
gate still covers everything" stands on construction (`governed_tool.py` wraps every tool; the
plan gate only adds a stop) and on `tests/test_a_plan_gate_only_ever_adds_a_stop.py`, not on a
measurement — said plainly.

## Metrics

* **Primary:** `sync_gap` — max consent-to-effect gap over S1's grid and S4's real runs, in
  seconds, beside the poll interval that bounds it.
* **Boundary:** `boundary_violations` (S2) — count, expected 0; `code_expiry_bound_holds` — bool.
* **Orphan:** `orphan_record_truthful` (S3) — the record says approved and the effect count is 0.
* **Named, not measured:** `plan_gate_drift` — unmeasured, with the join committed and the trigger
  named (the first recorded plan-gated turn).

## Decision rule (fixed now)

* **No revalidation is built for the synchronous path** if `sync_gap ≤ poll + 1 s` (S4's real
  runs included), `boundary_violations == 0`, and the code-expiry bound holds: the consent is
  consumed within one poll of being given, so there is no interval for it to go stale in. The
  mechanism's claim ships as measured, and G31-03's output is this bench plus the accumulated
  columns — not a revalidation feature nobody measured a need for.
* **Revalidation is built only for a surface a probe shows able to execute an effect different
  from the consent shown.** A violation in S1/S2/S4 (gap ≫ poll, a timeout over an existing
  answer, the code bound broken) is that finding, and the fix pre-registration names the surface
  the probe caught — never the population.
* **The orphan finding does not gate a fix by itself**: a record that says approved over an effect
  that never ran is the honest record of a false refusal (the person was acknowledged), and its
  remedy is the post-timeout reconciliation look, registered separately if S3 shows the record
  lying rather than the effect missing.

## Prediction (written before running)

S1: the gap is ≤ one poll interval at every grid point, and `seconds_to_answer` equals Δ exactly
at every point. S2: 0 boundary violations; the code bound holds at both ε. S3: the record says
approved in every orphan run and the effect count is 0. S4: the real gap lands ≤ 1 s (poll 0.2 +
scheduling); if it lands above poll + 1 s, that is a real finding about the poll loop under a real
scheduler, and it changes the decision rule's first clause. The plan-gate drift I expect to
confirm the gate's claim when it is eventually measured — every outside-plan action meeting its
own question — but that number is not produced here and this registration does not pretend it was.

## Amendment, 2026-10-05, after the first run — appended; nothing above changes

S3 as first written could not produce its number, and the first run proved it: the probe left the
waiter thread "abandoned" and counted the effect as never run — but the thread was never stopped,
so it consumed its own return and ran the effect (count 1). That is the mechanism working, not a
finding: the effect is the caller's own next line, and the only orphan window is between the
record's write and the return's consumption *inside the asking thread* — unreachable from outside
it. Killing the caller kills the effect with it; there is no in-process way to have both. What S3
now measures is the ordering it can reach: the record is written BEFORE the return (a record that
says approved while the waiter is still inside the ask), and the effect follows the return in the
same thread (exactly once, after the thread completes). The production orphan — a surface that
cancels its parked worker after the person answered — stays with the join named below
(`history.jsonl` says approved, `runs.jsonl` shows the turn stopped); it is a property of the
surface's cancellation, not of `pending.py`, and no probe here can produce it.

## What this cannot show (§2q)

* The plan gate's real drift — unmeasured until a plan-gated turn is recorded; the trigger and the
  join are committed, the number is not here.
* The ACP editor's standing grants — the editor's contract; a person who clicks "always allow" in
  a foreign editor has used that editor's consent mechanism, not ours.
* Production latency distributions — this machine's `approvals/` is empty; the columns G31-01 and
  G31-04 added are what will accumulate them, and the weekly review now prints them.
* Any model behaviour at all: no model is called, so nothing here says what an agent does with a
  refused or aged approval — that is bench/scenarios' population.

## Amendment, 2026-10-05, after the second run — appended; nothing above changes

S4's first version overrode the answer file's `answered_at` to the moment the answer was
SCHEDULED for, and run 8 caught the instrument lying: its answerer thread was scheduled a second
late (wall 2.00 s), and the override inflated the measured gap with the thread's own lateness —
production's `answer()` stamps the write moment, so the override measured a fiction. The probe now
leaves `answered_at` exactly as production writes it; the ten runs re-measured at ≤ 2.4 ms. The
registered prediction ("the real gap lands ≤ 1 s") held in both versions; only the instrument's
honesty changed.
