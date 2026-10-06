# The stale-consent probes — results

**2026-10-05.** Run against `PREREGISTRATION.md` (with its dated amendment, made after the first
run exposed a probe defect and before any reading was published). No model call anywhere, US$ 0.
- **Calls:** 0 model calls. 21 durable asks + 2 code answers + 10 real-clock asks, all against the
  real `chimera/governance/pending.ask_durably` with an injected clock (S4 on the real clock).
- **Raw data:** `results/run.json` — every probe's rows and the summary.

## The verdict, by the registered rule: NO REVALIDATION IS BUILT

| registered bar | measured | met? |
|---|---|---|
| `sync_gap ≤ poll + 1 s` (S1 fake, S4 real) | **1.5 s** fake (poll 2.0), **0.0024 s** real (poll 0.2) | yes |
| `boundary_violations == 0` (S2) | **0** — answers at limite−4 s … −0.5 s all approved; +0.5 s past it timed out | yes |
| code-expiry bound holds (S2b) | `applied` at −ε, `expired` at +ε | yes |
| → decision | **no revalidation for the synchronous path** | |

## What the numbers say

**The synchronous durable path cannot go stale by construction, and now it is measured, not
assumed.** The wait ends the moment the answer file appears; the gap between the person's yes and
the ask's return was 0–1.5 s under the fake clock (one poll interval, exactly the bound) and
**≤ 2.4 ms** across ten real-clock runs. There is no interval in which the consent can age before
the effect it governs runs. `seconds_to_answer` matched the scheduled answer moment exactly at
every grid point — the person's-clock column G31-04 reads is exact.

**The boundary is where the design said it would be.** An answer written one poll before the
deadline is honoured; one half-second past it is a timeout. The chat code stops one poll earlier
still (`expires_at = asked_at + wait − poll`), so a code acknowledged in the chat is never recorded
as a timeout — measured at both ε.

**The orphan probe's first reading was the probe lying, and it is recorded as such.** The first
run reported `orphan_record_truthful: false` with `effect_count: 1` — which looked like "the
record says approved and the effect ran anyway". Reading the probe before believing the number:
the "abandoned" thread was never stopped, so it consumed its own return and ran the effect. That
is the mechanism working. The effect is the caller's own next line; killing the caller kills the
effect with it, so the scenario as first registered is not producible in-process. The amendment
rewrote S3 to measure the ordering it can reach: the record is written **before** the return
(truthful under a surface that dies mid-ask), and the effect runs exactly once, after the thread
completes. The production orphan — a surface that cancels its parked worker after the person
answered — is a property of the surface's cancellation, not of `pending.py`; it stays with the
join named below.

## The named non-measurement: plan-gate drift

`plan_gate_drift_spec` is committed in `run.py` and **unmeasured**: this machine's home has no
recorded plan-gated turn (`approvals/` empty, checked 2026-10-05). The join is defined —
`history.jsonl` rows with `facts.tool == 'plan'` joined to `runs.jsonl` attempts by `run_id` —
and the trigger is named: the first recorded plan-gated turn. Until then, the claim "the plan gate
does not pre-approve anything" stands on construction (`governed_tool.py` wraps every tool; the
gate only adds a stop) and on `tests/test_a_plan_gate_only_ever_adds_a_stop.py`, not on a
measurement. The columns G31-01 and G31-04 added are what will accumulate the rows; the weekly
review now prints them.

## What this bench changes in the plan

G31-03's registered output is **this bench plus the accumulated columns — not a revalidation
feature**. The study's "medir stale consent antes de construir revalidação do efeito" resolves as:
measured; the synchronous path has no stale window; the two surfaces where consent can be old
(plan gate, ACP standing grants) are declared, the first with a committed join and a trigger, the
second out of scope as the editor's contract. Nothing ships.
