# Harness-Bench factorial — pre-registration

**Status: DRAFT, not started.** Item (b) of the study-16 plan
([`../PLAN-study16-eight-axes.md`](../PLAN-study16-eight-axes.md), §0 and the sequence table).
Written 2026-09-08 after a four-solve cost pilot and before any paid solve of the design below.
Nothing in this file is a result; §4 is the only measured section.

## 1 · Venue, and what it is not

[Harness-Bench](https://github.com/Qihoo360/harness-bench): 106 tasks, each a directory with
`prompt.txt`, `fixtures/`, a deterministic `oracle_grade.py` exposing `score_workspace(Path) ->
{"outcome_score": float}`, and an optional LLM rubric. **It is an agent-workflow benchmark, not a
coding benchmark** — `email-triage`, `ppt-brief-generation`, `hr-resume-screening`,
`medical-admin-claim-check` sit beside the coding tasks. Our factors are coding-shaped (a repo map is
inert on an e-mail), so the design runs on the **25 tasks whose id matches**

```
code|repo|pytest|test|migration|schema|api|ci-config|dependency|bug|parser|monorepo|compose|sql|debug|repair|coverage|frontend|js-|interface
```

fixed before any score was seen for 23 of them (two, 085 and 087, were the pilot). The list:
011, 016, 022, 039, 040, 041, 042, 043, 044, 045, 047, 051, 064, 078, 080, 082, 083, 084, 085, 086,
087, 088, 089, 092, 094.

**The repository has no licence file.** It is run privately, cited, and nothing from `tasks/` is
redistributed — the same posture as LoopsBench's "benchmark data must never appear in training
corpora", applied to a repository that did not say so.

## 2 · Apparatus, as it will run

| | |
|---|---|
| agent | `chimera` from **this repository at the commit named in RESULTS**, installed editable into a WSL venv — not the PyPI package, which predates #368 and has no `ending` |
| model | `openrouter/deepseek/deepseek-chat-v3.1` on every arm |
| invocation | Harness-Bench `generic_cli` adapter; each arm is one YAML entry under `models:` — no adapter code |
| fixed flags | `--max-attempts 1 --max-steps 120 --no-remember --no-collect --no-evolve-skills --no-manager --keep-workspace --max-usd 2.0`, `CHIMERA_HOST_EXEC=allow` |
| sandbox | the harness's per-run directory; **no OS sandbox** (bubblewrap is absent in WSL) — commands run on the host, which the log says once per solve |
| grading | the task's `oracle_grade.py` only. The harness's process/rubric grading needs its LLM proxy, which we bypass (`process_score: null`); `security_score` is recorded as the harness computes it |
| receipts | `CHIMERA_HOME` per (task, arm, replica) so each solve's `runs.jsonl` holds one receipt: USD, tokens, tools, `ending` |
| per-solve log | `start=<epoch>`, the agent's output, `rc=<exit code>`, `end=<epoch>` — **in the file**, because the pilot's `; echo rc=$?` made bash exit 0 and the harness recorded the echo's code, not chimera's |

Why `--max-steps 120`: at 40 both pilot arms used exactly 40 of 40 — the ceiling was deciding the
end, not the scaffold. At 120 the four pilot solves used 45–60 and stopped on their own. Any solve
that reaches 120 is counted (§6).

Why `--max-attempts 1`: retry is the object of a different measurement (item 1+7, step 2, the
matched-budget recovery sweep). Here every factor must act inside the one attempt — which is what
removed two of the four factors originally intended (§3).

## 3 · Design: 2³ full factorial, k = 3

Read from the code before choosing (`chimera/core/autonomous.py`):

| flag | where it acts | verdict |
|---|---|---|
| `--repo-map` | prepends a structural map to the worker's context before attempt 1 (`:723-728`) | **factor A** |
| `--checklist` | extracts the task's atomic requirements once, up front, and puts them in the worker's context on attempt 1 (`:734-766`) | **factor B** |
| planner (`--no-plan` off/on) | the planning step before the worker | **factor C** |
| `--progress-ledger` | runs *after a failed attempt* to steer the retry (`:1305-1330`) | **inert** under `--max-attempts 1` — excluded |
| `--diff-feedback` | shows a failed attempt its reverted diff *to the retry* | **inert** under `--max-attempts 1` — excluded |

The pilot's "full" arm carried all four flags and was therefore `repo-map + checklist` in effect.
Had the 2⁴ design run, half its factors would have been scored as null while never firing — the
§2r shape (report how much the intervention acted) caught by reading the help text.

| | |
|---|---|
| arms | 8 = every combination of A, B, C — **full** factorial, so main effects and all interactions are estimable, nothing aliased |
| tasks | 25 (§1) |
| replicas | **k = 3** per (task, arm) — the seeds rule from `chimera/eval/replicated.py`: two alert, three decide |
| solves | 8 × 25 × 3 = **600** |
| order | (task, arm, replica) triples shuffled once with a recorded seed; 6 solves run concurrently |
| harness ids | `arm-<ABC>-r<k>` — 24 entries, because the harness overwrites `results/<harness>/<task>.json` per (harness, task) and `CHIMERA_HOME` is per (task, harness) |

## 4 · The cost pilot — the only measured section

Four solves, two tasks × the two extreme arms, chimera at `d5f1d40`, `--max-steps 120`:

| task | arm | oracle | floor | loop's own verdict | US$ | in tokens | tools | wall |
|---|---|---:|---:|---|---:|---:|---:|---:|
| 085 | bare | 0.30 | 0.18 | `success` | 0.545 | 923k | 53 | 724 s |
| 085 | repo-map+checklist | 0.62 | 0.18 | `exhausted` | 0.492 | 835k | 48 | 687 s |
| 087 | bare | 0.91 | 0.29 | `success` | 0.408 | 707k | 45 | 459 s |
| 087 | repo-map+checklist | 0.91 | 0.29 | `exhausted` | 0.715 | 1,238k | 60 | 804 s |

- **US$ 0.540 per solve** (mean of 4; range 0.41–0.72). Projection for §3: **600 × 0.54 ≈ US$ 324**,
  with the scaffold arms above the mean (the pilot's scaffold arm cost +29% input tokens on 085 at
  the 40-step ceiling and +75% on 087 at 120). Hard stop at **US$ 400** spent.
- **Wall**: 8–14 min per solve; 600 at 6 concurrent ≈ **15 h**.
- **The loop's verdict is not the outcome.** `success` at 0.30 and `exhausted` at 0.62 and 0.91: on
  3 of 4 the loop's self-report points the other way from the oracle. The oracle is the dependent
  variable; agreement between the two is a secondary metric (§5), never a substitute.
- **One solve left no receipt** on its first run (087 bare: 194 s, one file edited, no receipt, no
  discarded diff, nothing in `CHIMERA_HOME`, exit code masked by the apparatus). Re-run alone it
  finished normally (rc=0, receipt, 0.91). It coincided with five parallel agents being launched on
  the host; the cause is not established. The design records `rc` per solve so a recurrence is
  visible rather than silent (§7).

**Oracle floor** — `score_workspace` on an untouched copy of each task's fixtures, computed for all
25 before any solve of the design. Doing nothing already scores this much:

| floor | tasks |
|---|---|
| 0.000 | 011, 022, 078, 080 |
| 0.03–0.10 | 043, 051, 064, 089, 092, 094 |
| 0.10–0.20 | 039, 042, 045, 047, 085, 086, 088 |
| 0.20–0.30 | 016, 041, 044, 082, 083, 084, 087 |
| **0.474** | **040** (`test-coverage-fill` — half the credit is free) |

Median 0.131, none ≥ 0.5, no oracle errored. Every task has headroom; 040 has the least, and the
`demo` adapter scores exactly the floor (it does nothing), which is how the floor was validated.

## 5 · Outcomes and analysis — fixed now

**Primary**: the oracle's `outcome_score` (continuous, 0–1), per solve. Analysed per task as the mean
over k = 3, then the factorial main effects paired by task: effect(A) = mean over tasks of
[mean(arms with A) − mean(arms without A)]. Bootstrap CI over the 25 tasks (10,000 resamples, seed
recorded). Two-factor interactions the same way. The floor is a per-task constant and cancels in
every paired difference; it is reported beside raw scores, not subtracted.

**Noise floor**, before any effect is read: the within-cell SD of the 3 replicas (continuous), and
on the thresholded outcome (`score ≥ 0.8` = pass) the per-task **flip rate**, **pass^k** and
**ICC(1)** from `chimera.eval.replicated`. An effect is reported as *inside the noise floor* when
|Δ| ≤ the mean within-cell SD, whatever its CI says.

**Secondary**: USD and input tokens per arm (the price of each factor); tools used per solve and the
count that hit 120; agreement between the loop's `ending` and the thresholded oracle
(self-report vs reality); `security_score` per arm; rc ≠ 0 and receipt-less solves.

## 6 · Registered predictions, and what would refute them

1. **Repo-map (A) helps where there is a repository to map**: on the 8 tasks with ≥ 10 fixture files
   (039, 042, 044, 083, 085, 087, 088, 092) effect(A) > 0; on the rest ≈ 0. Refuted if the
   ≥10-file subset shows |Δ| inside the noise floor, or a negative CI.
2. **Checklist (B) ≈ 0 on the oracle.** It targets dropped constraints in the loop's own grading; the
   oracle grades the tree. No directional claim; a positive effect outside the noise floor is the
   interesting outcome and would be reported as unpredicted.
3. **Planner (C): no prediction.** Recorded as such so a post-hoc story cannot become one.
4. **Noise**: within-cell SD ≥ 0.15 on at least half the (task, arm) cells; thresholded flip rate
   ≥ 25% (the floor measured on LoopsBench and Terminal-Bench).
5. **Cost**: arms with A or B cost ≥ +25% input tokens over the bare arm.
6. **Ceiling**: fewer than 10% of solves reach 120 tools; if more, the result is reported as
   ceiling-limited and the ceiling becomes a factor in a follow-up, not a footnote.

What it **cannot** show: anything beyond one model, one venue, 25 tasks, one machine without an OS
sandbox; anything about retries; effects smaller than the noise floor — which is the point of
measuring the floor first.

## 7 · Stop rules

- A solve with `rc ≠ 0` or no receipt is logged and re-run **once, alone**; if more than 5% of solves
  need that, the run halts and the apparatus is investigated before any number is read.
- US$ 400 spent: halt. `--max-usd 2.0` caps any single solve.
- Amendments to this file after the first paid solve record what had been seen.

## Reproduce (once approved)

```
# WSL — chimera from this repo, editable, in the bench venv
uv venv --python 3.12 ~/hb-venv && uv pip install --python ~/hb-venv/bin/python -e .
# arms: 24 generic_cli entries written into ~/harness-bench/config/harness.yaml by bench/harness_bench/write_arms.py
# run:  bench/harness_bench/run_factorial.sh  (shuffled triples, 6 concurrent, rc + epochs in each log)
# read: bench/harness_bench/read_results.py   (oracle scores + receipts -> chimera.eval.replicated)
```

The runner, the arm writer and the reader are committed with the RESULTS, not before: a runner that
exists before the design is approved invites "just a quick run".
