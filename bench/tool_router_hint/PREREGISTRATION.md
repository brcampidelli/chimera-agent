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

## Amendment 1 — approval, executors, cap and pilot (2026-09-23, before any solve)

**Approved by the owner: options (a) and (b) — all three executors** (strong `deepseek-v3.2`, weak `gpt-oss-20b`,
Sol `gpt-6-sol`), estimated ≈ US$ 37. The driver's own cap is **US$ 40**; reaching it halts new submissions and
the result is reported with the missing cells named.

**The ruler is frozen:** the arms run from a dedicated worktree at `e5bbc845` (the merge that shipped the hint
mode), installed into its own venv `hb-venv-b4b` — not the B4 venv, which points at a working tree someone else
edits (§2aa). Arm names `sys1h-{off,on}-{exec}-r{k}`, so no B4 result or home is reused.

**Pilot, unscored:** one `on` solve on `044-ci-config-repair` for the strong and the weak executor, read only for
the positive control — the router's receipt must show `hinted > 0` and no fallback storm — and deleted before the
scored run. If the pilot shows the hint never reaches the executor, the run does not start.

## Amendment 2 — the wrapper runs from $HOME; the pilot passed (2026-09-23, before any scored solve)

Setup found that the frozen venv listed the ruler (`chimera-b4b`) while `import chimera` resolved to the main
working tree: `wsl` starts in the Windows working directory, and `python -m` puts the current directory first on
`sys.path`. The wrapper now `cd`s to `$HOME` before the solve and writes `=== chimera=<path> ===` into every log,
so each solve carries a receipt of the code that ran. (B4's venv pointed at the main tree outright; that is a note
about B4's apparatus, recorded here and not re-litigated.)

Pilot (`044`, `on`, strong + weak, US$ 0.02): both logs name the frozen ruler; the router gave **13 and 4 hints,
narrowed 0, answered 0, fallbacks 0**; the executors followed 5/13 and 1/4. Positive control passed; the two
solves and their homes, logs and sandboxes were deleted, and the scored run starts clean.

## Amendment 3 — cap raised to US$ 45 (2026-09-23, at 24/414 solves, US$ 2.37, before any score was read)

At 24 solves the projected total was ≈ US$ 41, above the US$ 40 cap, driven by the Sol arm. The owner raised the
cap to **US$ 45** so the run can close complete. Only the spend ceiling changed; no outcome had been read.

## Amendment 4 — the run goes in foreground chunks (2026-09-23, before any score was read)

A detached driver (`nohup`, then `setsid`) died with the launching `wsl` session — the WSL VM shuts down when no
session is open. The driver runs in the foreground in 50-minute chunks (`timeout 3000`) and resumes: done cells are
skipped; a solve cut by a chunk's end has no result and is re-run in the next chunk. A first 40-second probe
started 6 solves that died with it; they left no result and are re-run like any other.
