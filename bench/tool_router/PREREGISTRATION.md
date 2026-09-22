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
