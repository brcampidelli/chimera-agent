# M6 fork — the effect of escalating on the runs the breaker actually stops. Pre-registration

*2026-09-24, written before any scored solve. Study 24, the follow-up `bench/tool_loop_escalation/RESULTS.md` §7 named. Approved by the owner on 2026-09-24, cap **US$ 20**.*

## Why

M6 compared two arms over all solves: stop when the breaker trips, or escalate to a stronger model. It came out a tie at that power. The breaker fired in about one solve in six, so most pairs compared two identical configurations, and replica noise buried the rest.

**What M6 did see, below the registered comparison.** On the strong executor, the three runs that escalated scored **0.726**. The three the breaker stopped scored **0.396**, on the same two tasks. That is n = 3 per side and was not registered.

This design measures that effect directly, and pairs it exactly.

## The fork

**The key fact.** When the breaker trips and the run is not escalated, the loop asks for one final answer **with no tools**. So the workspace at the trip is exactly what stopping leaves.

**The change under test** (this PR):
- `AgentConfig.snapshot_on_tool_loop` / `chimera solve --snapshot-at-tool-loop DIR`.
- When escalation fires, the workspace is copied to DIR, and then the run continues on the stronger model.
- **Off by default.**
- **Four unit tests hold:**
  - the copy is the state AT the trip, and nothing done after it leaks in;
  - it is taken once;
  - it is taken only when the run escalates;
  - an existing destination is never overwritten.
- **Sabotage-verified.**

**One solve gives both arms of a pair:**
- `stop` = the task's oracle on the snapshot;
- `esc` = the oracle score the harness published for the escalated workspace.

Both run the same function, `tasks/<task>/oracle_grade.py:score_workspace`. For all six M6 tasks it reads the workspace only, and the published score carries `outcome_llm_weight` 0.

A solve whose breaker never trips forms no pair. Those solves are paid and counted, and are never analysed as pairs.

## Design

**Tasks.** The four where step 1 (`bench/study24_counts`) counted the most breaker stops, chosen before any fork data:
- `041-frontend-state-bug` (16 of 88);
- `042-api-schema-migration` (8);
- `043-db-migration-safety` (8);
- `082-compose-config-repair` (8).

**Executors and escalation targets.** Same as M6:
- strong: `deepseek-v3.2` → `gpt-6-sol`;
- weak: `gpt-oss-20b` → `deepseek-v3.2`.

**Flags.** M6's wrapper and flags: `--max-attempts 1 --max-steps 120 --max-usd 2.0`, no memory, no collection, no evolution, no manager.

**How many.**
- Solves are drawn in a seeded order (seed **20260925**) over replicas × tasks.
- Each executor runs until it has **20 pairs**, or reaches its ceiling of **160** solves (strong) or **480** (weak), or the run hits the **US$ 20** cap.
- Every pair completed is analysed, including those still in flight when the target was reached. The stopping rule counts pairs, never looks at scores.

**Ruler.** A worktree frozen at this PR's commit, with its own venv (`hb-venv-m6f`). Every solve's log names the Chimera it imported, the workspace it used, and its snapshot path.

**Stop rules, as in M6:**
- the cap halts new submissions;
- a cell failing twice is frozen as **missing**;
- more than 5% `rc≠0`/receiptless after ≥ 20 solves halts the run for the apparatus.

## Gates before any score is read

1. **Copy check** (`grade_fork.py`). For every completed solve, the escalated workspace is copied the way the agent copies its snapshot, and the copy is graded. It must reproduce the published score within 0.001 in **every** cell. If one cell misses, the reader refuses to run (`read_m6f.py`). The stop arm is a graded copy, so a copy that grades differently would make each pair a comparison of two rulers.
2. **Contamination audit** over the fork's traces: M6's audit with the `m6f` pattern.
3. **Every log names the ruler.**

## Outcomes

**Primary, per executor.** The mean paired difference `esc − stop` over pairs, with a bootstrap 95% CI over pairs (seed 20260925).

**Secondary:**
- pairs where escalation helped, hurt or tied (|Δ| ≤ 0.001);
- Δ by task;
- the trip rate;
- US$ per solve, tripped against clean;
- the implied arm-level effect: trip rate × Δ. This is the number M6's design was measuring.

## Power, stated before the numbers

M6's six tripped strong runs suggest a per-trip difference near +0.3, but on n = 3 per side, unpaired. Assume a paired SD of about 0.3. Then 20 pairs give a standard error near 0.07, so the CI excludes zero for a true effect of about **+0.15** or more.

If a cap or a ceiling stops an executor short of 20 pairs, it is reported at the n reached. It is **not** extended.

## Predictions

- **Strong:** Δ between **+0.15 and +0.40**, with the CI excluding zero.
- **Weak:** Δ above zero, **+0.05 to +0.25**. The CI may include zero: weak runs trip early (about 7 steps in M6), with little done, and the target is a much stronger model.
- **Cost:** a tripped strong solve costs about **US$ 0.20 more** than a clean one (Sol continues the run). A tripped weak solve costs cents more.
- **Trip rate:** at least **10%** on strong on these four tasks, and at least **4%** on weak.

## Decision rule

- **The flag stays off by default in every case.** It adds a second model and its bill to any run that loops.
- **Documented as a recommended opt-in** for loop-prone work, if an executor's Δ has its CI low bound above zero. The recommendation names the cost per rescued point.
- **A tie** is published as a tie *at this n*. It is not published as "escalation does not help".
- **A negative Δ with the CI below zero:** the flag's docstring and `--help` say not to use it, with the number.

## What this cannot show

- **The final answer's text.** The oracles grade the workspace. In a real conversation a person also reads the stopped run's last message, and this measures none of it.
- **Tasks outside these four**, which were chosen for how often they trip.
- **Other escalation targets, or escalating on another signal.**
- **More than one escalation, or retries** (`--max-attempts 1`).
- **Anything the grading environment cannot run.** The same one as M6.
