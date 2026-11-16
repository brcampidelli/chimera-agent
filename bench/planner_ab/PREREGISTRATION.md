# Planner A/B — pre-registration (P1-01)

**Status: registered 2026-11-16, before the first paid call of this design.** Item P1-01 of
`study/auditoria-2026-11/PLANO-FINAL.md`. Venue and apparatus are `bench/harness_bench/` unchanged
(23 coding tasks, the benchmark's own `oracle_grade.py`, `chimera solve`, `--max-attempts 1
--max-steps 120`); only the planner flag and the model vary. Nothing below is a result.

## 1 · Question and the change it decides

`use_planner` is on by default on every autonomous path (`solve`, `/api/runs`, Kanban lanes,
workflow). `harness_bench` measured the planner at **+0.003** on the oracle score, inside the noise,
for **+3 %** cost — on one model (`deepseek-v3.2`), with a margin never declared (`PROTOCOL.md` §12
re-read says so).

The code change is small and ships regardless: `use_planner` becomes a per-run switch on lanes, on
the app's autonomous run and on workflow solve steps (default unchanged = on). **What this
registration decides is only the default**: whether a run that carries an executable verifier
(`verify` command) starts with the planner **off**. The default flips only if §5 says so; otherwise the
policy constant stays `False` with the number in its docstring.

## 2 · Design

Paired A/B on the same 23 tasks, **plan** (`chimera solve` as is) against **no-plan** (`--no-plan`),
nothing else different, k = 3 replicas per (task, arm, executor).

| executor | family | data | new spend |
|---|---|---|---|
| **E1 `deepseek/deepseek-v3.2`** | DeepSeek | **re-read of the stored 2026-09-12 factorial**, arms `000` (no-plan) vs `001` (plan), 23 × 3 per arm, committed in `bench/harness_bench/results/2026-09-13-factorial.jsonl` | US$ 0 |
| **E2 `z-ai/glm-5.3-flash`** | Zhipu | new run, 23 × 2 arms × 3 = **138 solves** | ≈ US$ 1–2 |
| **E3 `qwen/qwen3-coder-next`** | Alibaba | new run, same shape, **only if §6 allows** | ≈ US$ 2–4 |

Why these. The brief asked for two cheap executors the bench already supports. A fresh `deepseek-v3.2`
A/B would cost ≈ US$ 7 (138 solves at the factorial's US$ 0.05), over the item's US$ 5.00 ceiling, and
the 138 solves already exist. E2 and E3 are the two cheapest agentic-capable models of **different
families** on the OpenRouter list on the day of registration (`glm-5.3-flash` 0.04/0.08 and
`qwen3-coder-next` 0.12/0.80 US$/M tokens in/out; `minimax-m3.1-flash`, `gemini-3.5-flash-lite` and
`mistral-small-2603` are 3–10× dearer per solve). §14 asks for a second **family**; E1 + E2 are two.

**E1 is not fresh evidence and is registered as such.** Its aggregate planner effect was published
before this file (RESULTS.md: C = +0.003, [−0.025, +0.031] on the continuous score). What is new is the
reading — pass rate, a declared margin, a closed-form non-inferiority interval — and I have **not**
computed it before writing this section. The partition reads of 2026-09-15 are not repeated.

Arms for E2/E3 differ in exactly one flag. The fixed flags are the factorial's (`--no-remember
--no-collect --no-evolve-skills --no-manager --keep-workspace`, `CHIMERA_HOST_EXEC=allow`, the five
legacy switches pinned off via `bench/_legacy_defaults.py`), except **`--max-usd 0.15` per solve
instead of 2.0** (cost safety under the item ceiling; identical in both arms; every solve that stops on
it is counted and listed). Each (task, arm, replica) gets its own `CHIMERA_HOME` and harness id
(`p101-<model>-<plan|noplan>-r<k>`) so receipts and results do not collide with other benches that use
the same harness checkout. The (task, arm, replica) triples are shuffled once with seed **20261116**;
4 solves run concurrently.

## 3 · Outcomes

- **Primary**: *pass* = oracle `outcome_score ≥ 0.8` (the factorial's thresholded view, §5 there).
  Per task, the pass **rate** over its k = 3 replicas, per arm. Unit of analysis = the task:
  `d_t = rate_noplan(t) − rate_plan(t)`, 23 values per executor.
- **Secondary** (reported, do not decide): the continuous `outcome_score`, same pairing; USD per
  solve per arm and the ratio; solves that hit `--max-usd`; `rc ≠ 0` and receiptless counts; the
  task-level discordance table (tasks where no-plan is better / equal / worse).
- Validity gates, computed on the **plan** arm before the effect is read: an executor is *informative*
  only if its plan-arm mean pass rate is between **0.30 and 0.95** (a floored or ceilinged executor
  makes any non-inferiority claim vacuous — §13's trivial-agent point) and ≥ **95 %** of its solves
  finished with `rc = 0` and a scored oracle.

## 4 · Interval, margin, claim (§11, §12)

- **Interval (§11).** One-sample *t* over the 23 per-task differences, closed form
  (`chimera.eval.proportions.mean_t_interval` / `tost_mean`). No bootstrap decides. If an executor's
  differences show no spread the interval is undefined and the executor does not pass.
- **Claim and margin (§12).** **Non-inferiority, one-sided**, margin **−5 percentage points** on the
  pass rate: α = 0.05, i.e. the lower limit of the **90 %** two-sided *t* interval of
  `mean(d_t)` must lie **above −0.05**. Declared now, before any E2/E3 call and before the E1 pass-rate
  reading. No equivalence claim (the upper side is reported, not tested).
- Power, stated rather than hoped for: with 23 tasks and per-task differences whose SD on a binary
  outcome at flip rate ≈ 0.25 is ≈ 0.15–0.20, the 90 % half-width is ≈ 0.055–0.07. A true difference of
  zero can therefore **fail** the margin; if it does, the registered answer is "not adopted", not
  "re-run until it passes". The factorial's own bound (C on the score: half-width ≈ 0.026) suggests the
  pass-rate reading is the harder one.

## 5 · Decision rule (fixed now)

**Adopt "planner off by default when the run has an executable verifier"** iff **all** of:
1. at least two *informative* executors of **different families** were measured (E1 and E2, or E1/E2
   plus E3) — §14;
2. for **every** informative executor, the lower limit of the 90 % *t* interval of `mean(d_t)` on the
   pass rate is **> −0.05**;
3. no informative executor's plan-arm vs no-plan-arm USD per solve says no-plan is **dearer** by
   more than 5 % (adopting a cheaper-on-paper change that costs more would defeat its purpose).

Otherwise: **not adopted** — `use_planner` stays exposed and opt-in, default unchanged, the numbers
in the docstring. A mixed result (one executor passes, one does not) is "not adopted", and the
RESULTS says which one failed and by how much. If E3 is run and is informative, it joins condition 2;
it cannot rescue a failed E1 or E2. An executor that is not informative is reported and excluded from
the conditions (it cannot count as the second family).

## 6 · Cost, ceiling, stop rules

Ceiling for this item: **US$ 5.00 total, across everything**. Spend is read from the per-solve receipts
(`runs.jsonl` in each solve's home), not from the provider's key counter (the key is shared with other
items). A receiptless solve is counted at the running mean of its arm.

- **Pilot** = the first 12 triples of E2's shuffled queue. It is **kept** in the data (selection
  never looks at outcomes), and is read only for apparatus (`rc`, receipts, the route resolves,
  tool calls happen) and for cost per solve.
- **E2 projection** = pilot mean × 138; if it exceeds **US$ 2.50** E2 is cut to what fits as whole
  **tasks** (all 3 replicas × 2 arms, tasks taken in a fixed order = the order in `TASKS`) and the
  claim is made on that subset with the interval it gives — declared here, not after.
- **E3** runs only if, after E2 finishes, `5.00 − spent − 0.50 ≥ 138 × (E3 pilot mean)`, with E3's own
  12-solve pilot counted in `spent`. Otherwise E3 is **not run** and RESULTS says so; it is not
  partially run.
- **Hard stop**: halt new submissions at **US$ 4.00** spent (4 concurrent solves × per-solve cap leave
  the remaining US$ 1.00 as overshoot room). Never pass US$ 5.00.
- **Apparatus stop** (the factorial's §7): after 20 solves, if more than 5 % are `rc ≠ 0` or
  receiptless the run halts and the cause is investigated before any number is read; a solve that
  failed that way is re-run once, alone. A model route that does not resolve (all pilot solves fail)
  drops that executor, with the error in RESULTS.

## 7 · Controls (§13)

- **Trivial-agent arm**: not run again. The oracle floor per task (doing nothing: median 0.131, none
  ≥ 0.5) is already measured on these 23 tasks (`harness_bench/PREREGISTRATION.md` §4) and the
  paired difference cancels a per-task constant; the plan-arm 0.30–0.95 gate in §3 is the check that
  an executor is not sitting on the floor or the ceiling.
- **Random / matched-cost arm**: not applicable, no selection mechanism is evaluated.
- **Grader-hijack probe**: not applicable, the grader and the task trees are the factorial's, unchanged;
  `bench/verifier_integrity` already read those trees (0/323 tampering).
- **Attack-success, counterfactual-judge, placebo, rule-withdrawn arms**: not applicable (no attack, no
  judge, nothing is added to a prompt — the planner arm *removes* a step).

## 8 · Model scope (§14) and perturbations (§15)

The verdict names its models: any "planner off" conclusion is **measured on E1, E2 (and E3 if run)** and
is worded that way. Two families are required for the default to change (§5). It cannot speak to the
local routes (gemma, qwen3:4b), where outside evidence says planning helps weak models
(arXiv 2609.20804, cited in `harness_bench/RESULTS.md`).

§15: this registration claims **one configuration only**: `--max-attempts 1`, no `--verify` command in
the solve (the oracle grades, the agent is not told how), `--max-steps 120`. What it **cannot** show:
planner value on the retry path (`max_attempts > 1`, where the planner also re-plans after a stall),
on runs whose verifier gives the loop its own executable verdict, on non-coding turns, or on tasks
outside these 23. The default flip is scoped to runs *with* a verifier on the **assumption** that the
bench's oracle stands in for it; RESULTS states that assumption next to the verdict.

## 9 · Reproduction

`bench/planner_ab/run_ab.py` (driver; writes the 6 entries × 3 replicas per executor into the harness
config, runs the shuffled queue, stops on §6) and `bench/planner_ab/read_ab.py` (reads E1 from the
committed JSONL and E2/E3 from the driver log; prints §3–§5 and nothing else). Both are committed with
the RESULTS.
