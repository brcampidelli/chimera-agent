# M6 — when the tool-loop breaker trips: stop, or escalate to a stronger model? Pre-registration

*2026-09-24, written before any paid solve. Study 24, item M6 (`bench/PLAN-study24-jev-practice.md`); step 1 is
`bench/study24_counts` (#562). Approved by the owner; cap US$ 8.*

## Why

**Today's behaviour.** When the breaker trips, the loop asks once for a final answer, with no tools, and ends with `stop_reason="tool_loop"`.

**What step 1 counted** over 1,377 stored solves:
- the breaker ends **6.4%** of them, above the registered 5% line;
- those solves score **0.416**, against 0.681 for the rest;
- it concentrates on a few tasks and on the cheaper executors.

That gap mixes task difficulty with the cost of stopping. Only a paired arm separates them.

**The alternative, adapted from LiteLLM's `stall_escalation`:** move one tier up. Here that means handing the rest of the run to a stronger model, with every tool and a fresh detector. A second trip, on the stronger model, stops as today.

## The change under test

`AgentConfig.escalate_on_tool_loop` / `chimera solve --escalate-on-tool-loop MODEL` (this PR).
- **Off by default.**
- **Unit tests** hold three things: it is off by default, it fires once, and it is per run.
- **Sabotage-verified:** dropping "once" or dropping the model hand-off turns a test red.

## Design

**Tasks** — the six where step 1 saw the breaker fire most:

| task | breaker stops (of 88) |
|---|---:|
| `041-frontend-state-bug` | 16 |
| `042-api-schema-migration` | 8 |
| `043-db-migration-safety` | 8 |
| `082-compose-config-repair` | 8 |
| `086-sql-migration-preflight-rollback` | 6 |
| `087-cli-parser-bug-tests` | 6 |

**Executors and escalation targets:**

| executor | model | escalates to |
|---|---|---|
| strong | `deepseek-v3.2` | `gpt-6-sol` |
| weak | `gpt-oss-20b` | `deepseek-v3.2` |

**Arms:** `stop` (today) and `esc` (`--escalate-on-tool-loop <target>`). Otherwise identical: B4b's wrapper and flags (`--max-attempts 1 --max-steps 120 --max-usd 2.0`, no memory, no collection, no evolution, no manager).

**Replicas:** k = 3. **Total:** 6 × 2 × 2 × 3 = **72 solves**.

**Ruler:** a worktree frozen at this PR's commit, its own venv, and every solve's log naming the Chimera it imported (B4b amendment 2).

**Stop rules**, as in B4/B4b:
- the cap halts new submissions;
- a cell failing twice is frozen as **missing**;
- more than 5% `rc≠0`/receiptless after ≥ 20 solves halts the run for the apparatus.

**Before the score is read:** the step-1 contamination audit (`audit_contamination.py`) runs over the M6 traces.

## Outcomes

- **Primary:** per executor, the mean oracle score over the 6 tasks, paired by task (each task = mean over its 3 replicas): `esc` − `stop`, with a bootstrap 95% CI over tasks, read against the `stop` arm's own replica SD, measured here.
- **Secondary:**
  - the breaker's firing rate in each arm;
  - in `esc`, how many solves escalated, and their score;
  - cost per solve by arm, the stronger model's spend included;
  - pass@0.8.

## Power, stated before the numbers

- **Floor:** in B4b the strong executor's replica SD was **0.178**.
- **What that means:** with 6 tasks, this design sees an effect of roughly **≥ 0.15** per task mean. A smaller real effect will read as a tie.
- **The trade-off:** that is the price of spending US$ 8 rather than US$ 30, and a tie here is not evidence of no effect below that size.

## Predictions

- **Escalation helps both executors on these tasks**, by +0.05 to +0.15, with CIs that include zero. It rescues the runs that loop, and only those, and they are a minority of solves even here.
- **Cost:** the `esc` arm costs **20–40% more** per solve on the strong executor (Sol continues the stuck runs), and little more on weak.
- **Firing rate:** the breaker fires in **≥ 10%** of `stop`-arm solves on these tasks. Otherwise the tasks were not loop-prone after all, and the run can show nothing.

## Decision rule

- **The flag stays off by default in every case.**
- **Recommend it** (documented as an opt-in for loop-prone work) only if at least one executor's Δ is above zero with its CI excluding zero, and the other executor does not lose beyond its floor.
- **A tie** is published as a tie *at this power*, not as "escalation does not help".
- **A loss** means the flag is removed.

## What this cannot show

- tasks outside these six;
- other escalation targets;
- escalating on a signal other than the breaker;
- more than one escalation;
- retries (`--max-attempts 1`).
