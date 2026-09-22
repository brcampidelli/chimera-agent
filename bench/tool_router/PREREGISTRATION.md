# Pre-registration — B4: a "System One" tool router inside our own loop

*Written 2026-09-22, after the apparatus ran four paid pilot solves and before any scored solve of the design. Study 20 §3 B4 (`bench/PLAN-study20-calibrated-decisions.md`), the item Bruno released the budget for. The gate the plan put on it — "only if B1 shows a decision-first backend worth routing with" — is met: `bench/jev_decisions/RESULTS.md` §7b measured the decision-first local arm at AUROC 0.940 / 0.871 / 0.901 (easy / ambiguous / pooled), the hosted judge's own discrimination, at US$ 0 and 0.75 s.*

## 1. The hypothesis, which is not ours

Every "fast and slow" agent video states it: a small model, reading almost nothing, decides **which tool** the next step uses; the expensive model then only fills in the arguments. The claim is fewer steps and less money at the same pass rate, and the trade-off the videos themselves report is that chained work gets worse — a shallow router skips steps a deep reader would have taken.

It is stated precisely enough to measure, and this is the first item in the study that touches the loop rather than a corpus.

> **Does a cheap decision-first router, naming the tool before each step, change pass rate, cost or steps on our own tasks — and by how much against the noise floor of replicas of the same arm?**

## 2. The intervention, as it runs

`chimera/core/tool_router.py`, opt-in (`--tool-router MODEL`), shipped in the commit this bench cites:

* **Shallow context, on purpose.** The router sees the task (1,200 characters), the LAST output (1,200 characters) and the tool menu (name + first line). Not the conversation — a router that re-reads the transcript costs what the call it precedes costs, and the hypothesis is about a fast, shallow decision. This is the design choice the number is about; a deep router is a different experiment, and `tests/test_a_cheap_model_can_name_the_tool_the_step_uses.py` fails if the prompt ever grows to hold the conversation.
* **It narrows, it does not decide.** The executor still writes the call. A word that matches no tool leaves the full list — the step is then exactly what it would have been — and the receipt counts that as a fallback. `ANSWER` narrows to no tools at all.
* **Its spend is charged to the run.** Through the same gateway and the same ceiling. (The pilot found the defect that would have made this untrue: `CompletionResult` carries tokens, not money, so reading `result.usd` priced the router at 0.00 on every row — the arm under test would have been the cheap one by construction. Priced through `price_completion` now.)
* **Router model:** `openrouter/deepseek/deepseek-v4-flash-0731` (0.04 / 0.08 per 1M, the product default), temperature 0.

## 3. Design

| | |
|---|---|
| tasks | the 23 of `bench/harness_bench` (`run_factorial.py::TASKS`) — the same list #453 ran |
| arms | `off` (the loop as it ships) × `on` (`--tool-router`) |
| executors | **strong** = `openrouter/deepseek/deepseek-v3.2` (the factorial's model) · **weak** = `openrouter/mistralai/mistral-small-3.2-24b-instruct` |
| replicas | k = 3 per (task, arm, executor) — two alert, three decide |
| solves | 2 × 2 × 23 × 3 = **276** |
| fixed flags | `--max-attempts 1 --max-steps 120 --no-remember --no-collect --no-evolve-skills --no-manager --keep-workspace --max-usd 2.0`, `CHIMERA_HOST_EXEC=allow` — #453's, unchanged |
| grading | the task's `oracle_grade.py` only |
| order | (arm, task) shuffled once, seed 20260922, 6 concurrent |

**The apparatus is a clone, not the working tree.** `hb-venv-b4` is installed editable from the repository clone this branch lives in, because the main working tree carries another agent's uncommitted edits, and an arm built from a tree someone is editing is not a ruler (§2aa).

## 4. Two things the pilot already changed, stated before the run

1. **The grading environment is not #453's.** The preflight (`bench/harness_bench/preflight.py`, written after the 087 lesson) reported five tasks whose graders could not run here: `pytest` missing for 016, 087, 088 and `node` missing for 041, 084. Both are now installed, and the preflight is clean except `csvtool.cli` — the artefact 087's agent is meant to produce, correctly absent. **#453 ran without them**, so its absolute pass rates are not this run's baseline and its noise floor is not imported.
2. **The floor is measured inside this run.** The three replicas of the `off` arm give the replica-to-replica standard deviation on this executor, these tasks and this grading environment. That number, not #453's 0.073, is what every effect below is read against.

## 5. Outcomes — fixed now

Per (task, arm, executor), averaged over the three replicas, and paired by task:

1. **oracle pass** — the `outcome_score` the task's own grader returns (primary).
2. **cost** — US$ summed over the solve's receipts, with the **router's share printed separately**.
3. **steps** — the executor's step count.
4. **chained tasks** — the multi-round subset (`011-code-debug` is five rounds), read separately: the videos' reported trade-off is that a shallow router skips steps chained work needs.
5. **how much the router acted** — calls, narrowed, answered, fallbacks, and the pick histogram, from `tool_router.jsonl`. An intervention that cannot say how much it acted reads as "it did not help" when the truth is "it never fired" (§2r).

Effects are reported as the paired mean difference over the 23 tasks with a 95% CI by bootstrap over **tasks**, beside the `off` arm's own replica SD.

## 6. Predictions, written before any scored solve

- **P1 (strong executor).** Pass rate: a tie inside the floor — |Δ| ≤ 1 replica SD.
- **P2 (strong executor).** Steps: **+10% to +20%** with the router on (the plan's registered direction: a shallow router picks a cheaper tool and the loop takes more of them).
- **P3 (weak executor).** Pass rate: a **gain** above the floor — the half of the hypothesis that says routing helps a model that chooses badly.
- **P4 (cost).** The router's own spend is **under 10%** of the solve's total (measured in the pilot: US$ 0.000914 of US$ 0.006014 on `044-ci-config-repair`, 15%); the arm's total cost is **not** lower by more than the floor — a router that reads nothing cannot save the executor's input tokens, which are what a solve costs.
- **P5 (chained).** On `011-code-debug` the router arm is **no better**, and plausibly worse.

**Decision rule.** The flag stays opt-in regardless. It becomes a *recommendation* for weak executors only if P3 holds with the CI excluding zero **and** P1 holds for the strong one (no harm where it does not help). Anything else is published as measured, including a null.

## 7. Controls and stop rules

- **positive:** the `on` arm's receipts must show `narrowed > 0` on every solve; an arm whose router never fired is not an arm (the pilot read 11 of 11 narrowed, 0 fallbacks, on `044`).
- **negative:** the `off` arm must write no `tool_router.jsonl` at all (verified in the pilot).
- **paired:** each (task, replica) pair runs the same task, the same seed order, the same fixed flags, differing only in the flag.
- **stop rules,** #453's: US$ 25 halts new submissions; more than 5% of solves returning rc≠0 or receiptless (after ≥ 20 done) halts the run for the apparatus to be examined — which is how the pilot's missing `pytest` would have been caught had it not been caught by the preflight first.

## 8. Cost, measured rather than estimated

Four pilot solves on the strong executor cost **US$ 0.05** (US$ 0.0060–0.0177 each, mean 0.0125), 250–275 s each. At that rate 276 solves is **≈ US$ 3.5**, and the weak executor is cheaper per token. The registered ceiling is the approved **US$ 30**, the driver's own cap is set to US$ 25, and the expectation is that neither is approached. Wall clock ≈ 3.5 hours at concurrency 6.

## 9. What this cannot show (§2q)

- Anything about a **deep** router (one that reads the conversation) — a different instrument.
- Anything about these tasks under retries: `--max-attempts 1`, as in #453.
- A comparison of absolute pass rates with #453 — different grading environment (§4).
- Whether a *better* cheap model routes better: one router model, named above.
- Anything about the twelve tasks #453 found unable to distinguish any arm; they are kept so the task list is the registered one, and a null on them is expected.

---

## Amendment 1 — the weak executor is `gpt-oss-20b`, not `mistral-small-3.2-24b` (2026-09-22, before any weak-arm result was read)

The registered weak executor is **rate-limited upstream and cannot run**. The stop rule of §7 fired on its own at 2 bad solves in 22 — both on the weak arm, both dead in ~6 s with no receipt — and the log says why:

```
mistralai/mistral-small-3.2-24b-instruct is temporarily rate-limited upstream …
provider_name: Parasail, limit_source: upstream_provider_shared_pool   (HTTP 429)
```

Reproduced deliberately before changing anything, one call each through our own gateway:

| model | probe |
|---|---|
| `openrouter/openai/gpt-oss-20b` | **ok** |
| `openrouter/meta-llama/llama-3.3-70b-instruct` | **ok** |
| `openrouter/mistralai/mistral-small-3.2-24b-instruct` | **CredentialRejectedError** — every key rate-limited |

**The replacement is `openrouter/openai/gpt-oss-20b`** (weak tier, 0.03 / 0.13 per 1M, tools). Chosen over the 70B because the prediction under test (P3) is about a model that *chooses badly*, and a 70B is not that model; chosen before any weak-arm outcome was looked at, which is the only thing that keeps this an amendment rather than a result.

**What this costs the design:** P3 is now a prediction about `gpt-oss-20b`. The 12 weak-arm solves that ran before this amendment are discarded, not reused — a mixed arm is two executors averaged and reported as one (§2aa). The strong executor is untouched: its 16 scored solves stand, and the arm that produced them did not change.

**What did not change:** the router model, the tasks, k, the flags, the outcomes, the decision rule, and every prediction about the strong executor.

## Amendment 2 — a receiptless solve is re-run, not counted (2026-09-22, before any arm's totals were read)

The stop rule fired a second time: 5 of the last 25 solves returned **rc = 0 with no receipt**. Investigated rather than waived, and the cause is upstream, not ours — the solve log ends:

```
Timeout: litellm.Timeout: Timeout Error: OpenrouterException -
  litellm.Timeout: Connection timed out after 600.0 seconds.
=== rc=1 end=… ===
```

One provider call exceeded `CHIMERA_REQUEST_TIMEOUT` (600 s, the default), the CLI died, and `runs.jsonl` was never written — while the harness had already graded the workspace, so the row carries an outcome and no cost. It hit both arms and both executors (`on-strong`, `on-weak` ×2, `off-strong` ×2), so it is noise on the apparatus, not an effect.

**A second defect it exposed, worth more than the first:** the driver reads `proc.returncode` of `harnessbench.cli`, which exits **0** even when the command it ran died — so `rc=1` inside the wrapper reached the driver as `rc=0`. The receiptless count is the only reason these solves were noticed at all. That is the same shape the original pre-registration warned about (`; echo rc=$?` making bash exit 0) in a place it had not been checked.

**Changes, all before any total was read:**

1. `CHIMERA_REQUEST_TIMEOUT=1800` for the run — three times the default, still bounded.
2. **Resume now requires a receipt**, not just a result file: a receiptless solve is re-run instead of being counted as done. Without this, a solve that timed out was "finished" forever, and its arm silently lost a cost and a step count.
3. A solve still receiptless after the re-run is **excluded from the cost and step comparisons and counted in the results**; its outcome is reported separately. A cost mean over solves whose cost is missing is a mean over the solves that happened to succeed.
4. The stop rule now counts solves that remain bad **after** the re-run. The threshold (5%) and everything else are unchanged.

Nothing about the arms, the tasks, the router, the outcomes or the predictions changed.

## Amendment 3 — the request timeout is 900 s, and every halt is recorded rather than waived (2026-09-22)

Amendment 2 traded one failure for another, and this is the record of it. At 1800 s a single hung provider call can eat most of the wrapper's 2400 s budget, and then the whole solve is killed with neither a receipt nor an outcome: `043-db-migration-safety` on `sys1-on-strong-r0` ran 2400.3 s and produced nothing, while the median solve in that window took **107 s** and the 90th percentile **448 s**.

`CHIMERA_REQUEST_TIMEOUT=900` — above the 600 s that flaked, far enough below 2400 s that a hung call fails with time left for the solve to finish or for the retry to happen.

**The stop rule is not being loosened.** It has now fired three times, each time on something real (a rate-limited model; upstream timeouts; a hung call), and each halt is investigated, amended and relaunched rather than waived. What the results will carry instead of a moved threshold: **every halt, with its cause, and the number of (task, arm, replica) cells that never produced a receipt** — a cell missing from a cost mean must be visible in the same table as the mean.

**Retries are bounded:** the resume pass is run at most twice more. A cell still missing after that stays missing and is counted; it is not run until it succeeds, which would select for the solves that happen to be fast.

### Amendment 3a — the failures are one outage, not a rate (2026-09-22)

The fourth halt (4 bad in 24) looked like a 17% failure rate. It is not a rate. Every solve that has ever died on a provider timeout in this run — **all four of them** — ended within **four seconds of each other**:

```
4 solves that died on a provider timeout
  cluster of 4 within 4s
```

Two `off-weak`, one `on-weak`, one `on-strong`: every arm in flight at that moment, and nothing since. That is an upstream stall hitting the concurrent calls at once, so "5% of solves" is measuring how many solves happened to be in flight during an outage, not how often the apparatus fails.

Recorded rather than acted on: the threshold stays, the four cells are re-run by the resume pass, and **the clustering goes in the results** beside the missing-cell count. A failure rate quoted from correlated failures is a number about the concurrency, not about the thing that failed.

## Amendment 4 — the retry budget is a freeze list, and a relaunch continues the queue (2026-09-22)

Amendment 3 said "at most two more passes" and that sentence conflated two different things, which the fifth halt made obvious: **re-running a failed cell** and **continuing the queue**. The run is at 26 of 193 because each halt stops new submissions and a relaunch is needed to go on; freezing the whole run after two relaunches would leave 167 cells that were never attempted once, which is not a retry budget — it is an unfinished experiment.

Separated, and implemented rather than promised:

* **A cell that fails twice is written to `~/hb-frozen-b4.txt` and never run again** (`run_b4.py::freeze`). Running a cell until it succeeds selects for the solves that happen to be fast, and a mean over those is a mean over the easy half.
* **A relaunch continues the queue.** Cells never attempted keep running; frozen cells are skipped and printed at startup.
* **The results report both**: how many halts, their causes, and how many cells are frozen — with the frozen cells named.

The fifth halt's cause is the third amendment's again: 2 solves dying on a provider timeout **one second apart**. Every timeout in this run so far has come in such a cluster.

## Amendment 5 — the retry is spent inside the run, so an outage does not halt the queue (2026-09-22)

Sixth halt, same cause. The rule as written counts a failed solve against a 20-solve window, and every failure in this run has been a provider stall hitting the concurrent calls at once — **4 within 4 s**, then **2 within 1 s**. At concurrency 6 one stall is therefore always above 5%, so the run halted every ~22 solves and 168 cells were going to need eight more relaunches, each preceded by a drain.

The retry budget is **not** changed: two attempts, then frozen. What changes is where it is spent — a bad cell is **resubmitted once inside the same run**, and only a cell that fails **twice** counts toward the stop rule. So the rule now fires on what it was written to detect (a cell that cannot complete) rather than on how many solves happened to be in flight when a provider stalled.

Nothing about the arms, the tasks, the executors, the router, the outcomes or the predictions changed. The results report every halt, its cause, and every frozen cell by name.

## Amendment 6 — a third executor: `openai/gpt-6-sol` (2026-09-22, approved at US$ 38, before any Sol solve)

The partials raised a question the two registered executors cannot answer — *what does the router do to a model that is actually good?* — and the frontier prices moved the same day, so it is answerable for the price of the run itself.

**Executor:** `openrouter/openai/gpt-6-sol`, $2 / $10 per 1M, **cache read $0.20/M**, 1.1M context, **released 2026-09-22** — the day of this run. Standard routing (not `Exacto`, OpenRouter's tool-calling-accuracy mode), so it is the same routing the other arms got.

**Cost, from the token counts this run measured** (279,954 prompt / 8,460 completion / 223,244 cache-read per solve on `off`; 131,369 / 14,549 / 51,642 on `on`), at the posted cache price rather than an assumed discount:

| | US$/solve |
|---|---:|
| `off` | 0.243 |
| `on` | 0.315 |
| **138 solves** | **US$ 38** |

Everything else is the registered design: the same 23 tasks, the same two arms, k = 3, the same flags, the same router model, the same freeze rule.

### Predictions for this arm, written before it ran

- **S1 — the sign of the cost effect INVERTS.** On `deepseek-v3.2` the router cut cost 58%; here the `on` arm should cost **more** per solve (0.315 against 0.243). The mechanism is in the numbers above and not in the model: the control reuses **223k cached tokens per solve**, the router arm only **52k**, and at $2/M fresh against $0.20/M cached the lost cache outweighs the saved steps. If this is wrong, my account of *why* the router looked cheap is wrong too.
- **S2 — steps still fall, by less than 37%.** The mechanism (the router says `ANSWER`, the loop stops early) does not depend on the executor; but Sol's own card claims it "completes comparable tasks in fewer steps and with fewer tokens", so the control arm starts closer to the floor and there is less to cut.
- **S3 — the outcome effect is no better than the strong arm's.** A better executor loses more by being interrupted, not less; and the control's absolute score should sit **above** `deepseek-v3.2`'s 0.714, which is also the check that the arm is really running a better model.

### Two risks, named rather than discovered later

1. **The model is one day old.** OpenRouter shows "not enough performance data" for it, and this bench has already been halted six times by provider stalls. The freeze rule (amendment 4/5) is what keeps that from becoming a silent hole; the frozen count goes in the results.
2. **A third executor is a third comparison, not a factor.** It is read as its own paired off/on contrast, never pooled with the other two — three executors averaged into one number is the §2y shape this project has already been bitten by.
