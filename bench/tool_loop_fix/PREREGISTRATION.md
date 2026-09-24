# The loop breaker, legacy against fixed, on coding tasks. Pre-registration

*2026-09-24, written before any scored solve. Study 24; the owner asked for the fix and a re-measurement the same day. Cap **US$ 10**.*

## Why

**The fix.** Commit 9c92637 makes the breaker require the same call **and** the same answer. It was written after two measurements the same day showed each old rule's false alarm:

- **`bench/browser_viewport_tasks`:** all 20 failures viewport-first added were five `scroll` calls stopped by the args-only rule.
- **`bench/tool_loop_fork`:** all 20 breaker trips of the strong executor were four **successful, distinct** edits stopped by the output-only rule. They were not stuck.

**The question left open.** The fork compared stopping against escalating to a stronger model, so it cannot say what happens if the run is simply **not stopped**. This measures that, on the same kind of task.

## Design

**Arms.** The arms differ only in which frozen Chimera the wrapper imports:
- `legacy` — `origin/main` at e6551c5;
- `fixed` — this branch, whose only change under `chimera/` is `chimera/core/tool_loop.py`.

Both run with no escalation. Everything else is M6's wrapper and flags: `--max-attempts 1 --max-steps 120 --max-usd 2.0`, with no memory, no collection, no evolution and no manager.

**Tasks.** The four where step 1 counted the most breaker stops, as in the fork:
- `041-frontend-state-bug`
- `042-api-schema-migration`
- `043-db-migration-safety`
- `082-compose-config-repair`

**Executors.**
- strong: `deepseek-v3.2`
- weak: `gpt-oss-20b`

**Size.** k = **10** per task, arm and executor, for **160 solves**, in a seeded order (seed 20260926).

**Stop rules, as in M6:**
- the cap halts new submissions;
- a cell failing twice is frozen as missing;
- more than 5% `rc≠0`/receiptless after ≥ 20 solves halts the run for the apparatus.

**Gates before any score is read:**
- the contamination audit with the `brk` pattern;
- every log names its arm's frozen tree.

## Outcomes

**Primary, per executor.** The mean oracle score of `fixed` minus `legacy`:
- each task is weighted equally;
- the CI is a 95% bootstrap that resamples solves within each (task, arm) cell, 10,000 draws. The arms are independent runs, not pairs;
- it is read against the legacy arm's replica SD, measured here.

**Secondary:**
- the share of solves the breaker ended, per arm;
- the pattern of the last four calls in each stopped run: tools; args the same or distinct; output the same or distinct;
- US$ and steps per solve;
- the per-task means.

## Power, stated before the numbers

The fork gave the strong executor a trip rate of 0.286 on these tasks, and a loss of about 0.47 per trip when stopped. If not stopping recovered even half of that, the arm-level gain would be about **+0.07**. The fork's measure was **+0.13**, but that continuation ran on Sol.

With k = 10, a replica SD near 0.24 gives a CI half-width near **0.10** over four tasks. So a +0.07 gain may read as a tie, and a +0.13 gain should not.

## Predictions

- **Strong.** The breaker ends **≥ 15%** of legacy solves and **≤ 5%** of fixed ones. The score rises by **+0.05 to +0.15**.
- **Weak.** The breaker stops about the same share in both arms: the weak executor's trips were real failures, malformed calls that the fixed rule still catches. Δ within **±0.05**.
- **Cost.** Fixed strong solves cost **more** (+20% to +60%), because runs that used to be cut now continue.
- **Legacy stops.** Most legacy stops on strong show *distinct args · same output*, the edit pattern.

## Decision rule

**The fix is merged if all three hold:**
1. neither executor's Δ has its CI **low bound below −0.05**;
2. on strong, the fixed arm's breaker share is **lower** than the legacy one;
3. no fixed stop shows *distinct args · same output* with a successful output, which is the pattern the fix removes.

**A gain with its CI above zero** is reported as the fix's value.

**A loss beyond −0.05** on either executor blocks the merge, and the rule is revisited.

## What this cannot show

- tasks other than these four;
- surfaces other than `solve`: chat and the desktop app use the same detector, and are not measured here;
- how much of the fork's +0.47 per trip came from Sol rather than from not stopping. The arms here never escalate, but the tasks and the model are not the fork's pairs.
