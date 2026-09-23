# Pre-registration — B4b: the router as a hint, never a gate

Study 22, phase 6 (`bench/PLAN-study22-system-one.md`). Written 2026-09-23, **before any scored solve**.
The spend is not approved yet; nothing runs until it is, and the approved figure is added here first.

## 1. Why B4 is not the answer to "does a System One router help"

B4 (`bench/tool_router/RESULTS.md`) measured the narrowing router worse on every executor — −0.087 / −0.194 /
−0.307 oracle score — and named the mechanism: `ANSWER`, the router's power to end the loop, tracked the damage
(3.0% → 6.1% → 6.8% of steps, the order of the loss). But the B4 router also **removed tools** on every other
step, and the design could not separate the two. Both break study 22's direction rule (I1): a decision may add
scrutiny or information, never remove capability or stop work.

## 2. The intervention

`chimera/core/tool_router.py`, `mode="hint"` (`--tool-router MODEL --tool-router-mode hint`):

* **No tool is removed.** The executor gets every tool on every step.
* **No `ANSWER`.** The router is not offered it; a model that says it anyway is a fallback, and nothing happens.
* **The suggestion rides on one step** as a system line — "the next step may need `X`; use any tool, or none" —
  on a copy of the history, so it is never read back later as something that happened.
* **The router sees progress:** the tools already used this run (last 12), beside the task and the last output.
* **It reports how much it acted (§2r):** calls, hints given, hints the executor followed, fallbacks, its own spend.

Same router model as B4 (`deepseek-v4-flash-0731`), same shallow context otherwise.

## 3. Design — B4's, unchanged

The 23 tasks of `bench/harness_bench`; arms `off` × `hint`; k = 3; B4's fixed flags and B4's amendments 2–5
(receiptless re-run, 900 s request timeout, freeze list, in-run retry), all as registered there. The `off` arm is
**re-measured**, not imported from B4: another day, another route state (study 21 §2ae).

**Executors — to be fixed with the spend approval, one of:**
* **(a)** strong `deepseek-v3.2` + weak `gpt-oss-20b` — B4's two cheap executors. **≈ US$ 8.**
* **(b)** (a) + Sol `gpt-6-sol` — where B4's damage was largest. **≈ US$ 37.**

Estimated from B4's own per-solve bill (`results.json`): control mean US$ 0.053 (strong), 0.0015 (weak), 0.204
(Sol). B4's treated arm was cheaper only because it stopped early; a hint arm is priced at the control's cost plus
the router's (8% strong, 37% weak), 69 solves per arm per executor.

## 4. Outcomes

Per (task, arm, executor), mean over the replicas, paired by task: oracle score (primary), pass at ≥ 0.8, cost,
steps; the chained task `011` separately. Effects as the paired mean difference over the 23 tasks with a
bootstrap 95% CI over tasks, beside the `off` arm's own replica SD.

## 5. Predictions (written before any scored solve)

* **P1.** Score: a **tie** inside the floor on every executor — |Δ| ≤ 1 replica SD. The hint removes the two
  mechanisms that did the damage; nothing in it adds capability the executor lacks.
* **P2.** The executor **follows** the hint on more than half the hinted steps — the router picks what a person
  would (B4: `read_file`, `run_shell`, `list_dir`, `write_file`), which is usually what the executor would pick.
* **P3.** Steps and cost: **no saving** beyond the floor. The hint does not shorten work; the router's own
  spend is added (B4: 8.0% of the solve on strong, 37.3% on weak).

## 6. Decision rule

* The flag stays opt-in in every case.
* **Recommend the hint mode for an executor** only if its score gain is above the floor with the CI excluding
  zero, and no other executor loses beyond its floor.
* **A tie is the expected result and closes the question:** the damage B4 measured came from the power to stop
  and to remove, and a router that has neither does not pay for itself. The plan's §5 row "Tool router" is then
  closed as *never as a gate; as a hint, no effect*.
* **A loss** would mean the hint misleads — published, and the mode removed.

## 7. What this cannot show

Hint content other than one tool name; a deep router (whole transcript); retries (`--max-attempts 1`); tasks
outside the 23.
