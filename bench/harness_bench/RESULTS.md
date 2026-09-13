# Results — none of the three harness factors beats the noise floor on the oracle, and each costs 10–16%

Run 2026-09-12/13 against [`PREREGISTRATION.md`](PREREGISTRATION.md) and its amendments (model
`deepseek-v3.2`; 078/088 dropped for the public-tunnel prerequisite → **23 tasks × 8 arms × k=3 = 552
solves**). Total spend **US$ 29.28** on 552 solves (an order of magnitude under the US$ 400 stop, and under the ~US$ 205
v3.2 projection). Reader: `read_results.py`; per-solve rows in `~/hb-driver.jsonl`, oracle scores in
the harness's `data_try6/results/<arm>/…`.

## Verdict: a null with power — simplify, because none of the scaffolding earns its cost

Main effects on the oracle's `outcome_score`, paired by task, bootstrap 95% CI over the 23 tasks:

| factor | Δ (with − without) | 95% CI | vs noise floor |
|---|---:|---|---|
| **A · repo-map** | **−0.012** | [−0.036, +0.010] | inside (|Δ| < SD) |
| **B · checklist** | **+0.005** | [−0.052, +0.052] | inside |
| **C · planner** | **+0.003** | [−0.022, +0.029] | inside |

**Noise floor:** mean within-cell SD **0.073** (184 cells with k≥2); thresholded (≥0.8) flip rate 0.25.
Every effect is smaller in magnitude than the replica-to-replica noise, and every CI includes zero.
This is not an underpowered null: the SD is 0.073, so the design could see an effect of ~0.07+, and
there is none. On this model and these 23 coding tasks, **repo-map, checklist and the planner do not
move the outcome.**

## Cost — the factors are not free

USD per arm (mean per solve), and the point of the whole exercise:

| arm (A B C) | mean US$/solve | vs bare |
|---|---:|---:|
| 000 bare | 0.0502 | — |
| 001 planner | 0.0517 | +3% |
| 010 checklist | 0.0582 | +16% |
| 011 checklist+planner | 0.0514 | +2% |
| 100 repo-map | 0.0531 | +6% |
| 101 repo-map+planner | 0.0522 | +4% |
| 110 repo-map+checklist | 0.0550 | +10% |
| 111 all | 0.0563 | +12% |

Each factor adds ~3–16% to the per-solve cost and returns nothing measurable on the oracle. That is
the harness-engineering lesson made a number (`refs/harness-engineering.md`: *a good harness gets
simpler; every component encodes an assumption about what the model can't do alone, and they age*).
On `deepseek-v3.2`, these three assumptions have aged out for this task class.

## The simplify implication, and the one decision it points at

Of the three, only the **planner is on by default** in production (`--repo-map` and `--checklist` are
opt-in). The planner's effect here is **+0.003, inside the noise**, at a cost — so for this class of
coding task on this model, **`--no-plan` is the defensible default**, and turning repo-map/checklist on
is not worth it. This is a recommendation to the owner, not an auto-flip: the planner default is
global (it touches non-coding turns this venue never exercised), so the honest move is to disable it
for the measured class or A/B it in production, not to change the global default on 23 coding tasks
alone. What this bench establishes is that the *coding-shaped* scaffolding is not paying for itself.

## Registered predictions, and which the run refuted

1. **Repo-map helps where there is a repo to map — REFUTED.** On the ≥10-fixture-file subset (039,
   042, 083, 085, 087, 092) repo-map Δ = **−0.012**, identical to the <10-file subset and inside the
   noise. No help even where the map has the most to say. Published as wrong.
2. **Checklist ≈ 0 on the oracle — HELD.** Δ +0.005, inside the noise.
3. **Planner — no prediction (as registered).** Δ +0.003, inside the noise.
4. **Noise floor — partly refuted.** Predicted within-cell SD ≥ 0.15; measured **0.073** — v3.2 is
   *more* consistent than the LoopsBench/Terminal-Bench floor predicted. Thresholded flip rate 0.25,
   as predicted.
5. **Cost ≥ +25% for A/B arms — REFUTED (smaller).** Measured +6–16%, not +25%. Real, but less than
   registered.
6. **Ceiling <10% of solves at 120 steps — not captured.** The chimera receipt records `usd`,
   `ending`, `attempts` but not the per-solve step count, so the ceiling fraction cannot be read from
   the artefacts. No solve hit the US$ 2 per-solve cap; the outcome numbers are unaffected.

**Self-report vs reality (secondary):** the loop's `ending == "success"` agrees with oracle ≥ 0.8 on
**331/547 = 61%** — the pilot's observation holds at scale: the loop's own verdict is not the outcome.

## Apparatus notes (the stop rules and anomalies, so the number is read honestly)

- **The first launch halted itself** on the §7 rule (4/25 rc≠0-or-receiptless): 078 and 088 need a
  public tunnel absent here (amendment 2). Excluded; the rule worked before any number was read.
- **One cell** (080 / arm-101-r2) failed once with an `AttributeError` inside the task's own oracle
  (`'list' object has no attribute 'get'`) on a specific workspace state; re-run alone it succeeded (rc=0, outcome 0.739); 080 scored on every arm. Final set is 552/552.
- **Five receiptless-but-rc0 solves** (044 ×1, 087 ×4): the agent finished (outcome captured) but no
  receipt was written — the pilot's rare concurrency anomaly. Their outcome is in; their USD is not,
  so the per-arm cost means omit 5 of 552 cells (~0.9%). Never scored 0; §2.

## What this cannot show
- **One model (`deepseek-v3.2`), one venue (23 Harness-Bench coding tasks), one host, no OS sandbox,
  no retries.** A stronger or weaker model could make the scaffolding matter; `--max-attempts 1` means
  every factor had to act inside one attempt (progress-ledger and diff-feedback were excluded as inert
  under one attempt, §3).
- **The oracle is the DV.** The harness's LLM rubric/process grade was bypassed (no proxy), so this is
  outcome-faithfulness, not prose quality.
- **A null on the outcome is not "the scaffolding never helps"** — it is "on this model and this task
  class it did not, at a measurable cost," which is exactly the case for turning it off here.
