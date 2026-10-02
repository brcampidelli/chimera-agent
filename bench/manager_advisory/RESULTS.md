# The Manager as a gate, or as a note — results

2026-10-02 · pre-registered in `PREREGISTRATION.md` (committed before `census.py` ran) · **US$ 0**, no
model called · every number from files tracked in git · `results/census.json` holds all of them.

## Verdict: **the stored data cannot answer the run-level question — nothing ships**

D1 (advisory as the default) fails its bar by arithmetic. D2 (advisory as an opt-in setting) needs the
run-level counterfactual, and **no stored row holds the work the gate reverted**, so D2 cannot be
evaluated. Per the rule, no product code changes. What *can* be said is below, and it is not nothing.

## C1 — receipts: **0 labels in 1,056 files**

No tracked `.json` / `.jsonl` under `bench/` carries an attempt `evidence` value (`verifier`,
`diff+manager`, `manager`, `diff`, `none`). Benches keep pass/fail, cost and tool names; the receipts
that name the deciding gate lived in per-run `CHIMERA_HOME`s, temporary or outside the repository.
Prediction held.

## C2 — configuration: the Manager decided **186 of 2,726 stored solves (6.8%)**

| bench | solves | Manager | executable verifier | Manager decides every attempt |
|---|---:|:-:|:-:|:-:|
| learning_lift | 1,140 | on | **on** | no |
| harness_bench factorial | 552 | off | off | no |
| retry_lift | 360 | on | **on** | no |
| local_lift journals | 212 | half the arms | **on** | no |
| **swe_bench treatment arms** | **126** | **on** | **off** | **yes** — but no run verdict stored, prose-only Manager (pre-#244) |
| test_gate_two_sided | 98 | off | off | no |
| swe_bench baseline arms | 82 | off | off | no |
| edit_tools | 62 | on | **on** | no |
| **tool_defer** | **60** | **on** | **off** | **yes** — run verdict and oracle both stored |
| unattended_claims | 34 | off | off | no |

My prediction was 10–20%; it is **6.8%**. Every bench that measures a capability hands the loop a
`--verify` command or turns the Manager off, deliberately — so this number describes how this project
*builds benches*, not how often a desktop or terminal run reaches the Manager path. That production
frequency lives in `runs.jsonl` on the machines that ran them and was out of scope (see the end).

## C3 — `tool_defer`, the one place the gate's decisions meet an oracle

60 runs, 0 halted, one model, Manager on with the diff as evidence, no verifier, workspace guard on:

| run verdict \ oracle (task's own shell verifier) | passes | fails |
|---|---:|---:|
| **approved** (exit 0) | 33 | **4** |
| **failed after 3 attempts** — every attempt reverted | 0 | 23 |

* **Approved runs: 33/37 = 0.89 pass.** The four that do not: three `page_words` answers that wrote `NAO` for a
  page the verifier says does mention the phrase, and one `json_chart` answer that wrote a script and said
  *"to generate the chart, you need to run …"* — approved on a diff holding `generate_chart.py` and no
  `grafico.png`.
* **23 of 60 runs (38%) ended with every attempt reverted**, and every one of them fails the oracle — by
  construction, since the oracle ran on the reverted tree. All 23 called a write tool at least twice.

(As disclosed in the pre-registration, this table was glimpsed once before it was registered; read it as
a description, not a confirmed prediction.)

## C4 — identifiability: **no**

The rows store `completed, exit, steps, tools, tail, …` — no diff, no attempt list, no `discarded_at`.
The reverted work *was* preserved: several tails print the path, `…/home/discarded/<id>-3.diff`. But the
home was a `TemporaryDirectory`, and it went with the run. So whether the 23 reverted runs held correct
work is not recoverable from anything kept.

**The descriptive half, read by eye (§2e), and why the script's count is not the reading.** The script
counts failed runs whose final answer *mentions* the expected value: 10 of the 10 failed runs on the four
tasks with a single-value answer (`csv_total`, `primes_sum`, `stats_median`; `doc_field` never failed).
Read raw, two of those ten say they could **not** write the file (*"you would need to … create total.txt
with just 45.50"*, *"Since I cannot execute code to create the file directly"*), and a third shows its
`write_file` as leaked tool-call text in the prose rather than as a call. **Seven** claim to have written
the right value and were reverted anyway. A claim is not evidence — 36% of the claimed successes in the
`manager_diff` corpus were false — so this is the size of a question, not an answer to it.

## C5 — the corpus: control reproduces, D1 fails

The control reproduced exactly: **47/246** and **2/139** from `manager_diff.jsonl` (§2aa). The corpus is
exactly the solves the non-Manager gates let through, so an advisory Manager approves all of it.

| | gate TPR | gate FPR | advisory TPR / FPR | ΔTPR | ΔFPR | D1 bar |
|---|---:|---:|---:|---:|---:|---|
| all 385 (registered) | 0.191 | 0.014 | 1.00 / 1.00 | **+0.809** | **+0.986** | ΔTPR ≥ +0.20 ✅ · ΔFPR ≤ +0.05 ❌ |
| 262 with productive evidence | 0.239 | 0.031 | 1.00 / 1.00 | +0.761 | +0.969 | — |

**What a note would tell its reader** — the part that would decide whether an advisory mode is worth
anything over the existing `--no-manager`:

| | all 385 | 262 productive |
|---|---:|---:|
| P(false success) — base rate | 0.361 | 0.248 |
| P(false \| Manager says **APPROVE**) | **0.041** | 0.041 |
| P(false \| Manager says **REVISE**) | 0.408 | 0.296 |
| REVISE lift over base [95%, task-clustered] | +0.047 [0.021, 0.074] | +0.047 [0.012, 0.081] |

The Manager's two words are not worth the same. An **APPROVE is strong evidence** (4% false against a 36%
base). A **REVISE moves the base rate by under five points** — it is issued on 199 true successes and 137
false ones alike. A REVISE note on a receipt would be correct about the work's falseness about as often as
the receipt already is without it. My prediction (lift within 0.10 of base; APPROVE strong) held.

## What this means, and what it does not

* **The gate is the 0.89-precision path, and its cost is invisible in stored data.** Where it can be
  checked (`tool_defer`), the runs it approves are mostly right; it also ended 38% of runs by reverting
  everything, and the evidence of whether that work was right was deleted with the temporary home. The
  `manager_diff` corpus says the REVISE that drives those reverts is barely informative — at the attempt
  level, with one attempt. Retries change that arithmetic and are exactly what no stored row records.
* **No product change.** D1 fails; D2 cannot be evaluated; the rule says ship nothing, and nothing ships.
  `use_manager` stays on by default, and `--no-manager` remains the way to get "no veto" today.

## The measurement that would answer it

Any one of these, each cheap:

1. **The production census** (US$ 0): count attempts by `evidence` in `runs.jsonl` on the PC and the VPS,
   and how many REVISE verdicts were followed by a revert. That is the frequency this bench could not see.
2. **`tool_defer` re-run with the home kept** (≈ the original run's spend): the same 60 solves, keeping
   `CHIMERA_HOME` per run so each reverted attempt's `discarded/*.diff` can be applied to a copy of the
   workspace and graded by the same shell verifier. That makes C4 identifiable, and D2 decidable as written.
3. **The paired live A/B** study 28 proposed (gate vs advisory, no `--verify`, 3 replicas), primary
   *correct attempts reverted* and *false successes accepted* — after (1), and only if (1) shows the path
   is reached.

## What this cannot show

Production frequency (above). The verifier-abstained case — a `--verify` that could not judge, handing
the vote to the Manager — leaves no trace in stored rows. `tool_defer` is one model, ten small tasks; the
SWE-bench treatment arms are in the regime but stored only the final patch, with `--keep-workspace`, from
before the Manager saw the diff. The Manager's own replay / paraphrase floor was never measured on this
corpus (§5).
