# The Manager as a gate, or as a note — pre-registration

Study 28, item MA2 (and R1 of the review/subagents list). Written 2026-10-02, **before the census
below is computed**. US$ 0: no model is called; everything is read from files already in the repository.

## Question

`bench/manager_diff` measured the solve loop's Manager, shown the attempt's diff, on 385 claimed
successes: it approves **47/246** true ones and **2/139** false ones. Its own RESULTS.md left the
production question open: *how often does the Manager path decide a real run, and what does a rejection
cost there?* In the loop (`chimera/core/autonomous.py`), when no executable verifier ran, `ok` is the
Manager's verdict; a REVISE fails the attempt, reverts the workspace (the work goes to `discarded_at`),
and spends another attempt.

An **advisory** Manager would still be asked, but its verdict would be written on the receipt as a note
and would never fail, revert or retry an attempt; the deterministic gates (diff, contract, checklist,
strong verifier) would keep deciding. Two questions:

1. **Census.** In the stored runs, how often was the Manager the deciding gate with no executable verifier?
2. **Counterfactual.** What would the advisory Manager have changed — correct work kept, false successes
   let through — and can the stored data say?

## What is read (all tracked in git)

* **C1 — receipts.** Every tracked `.json` / `.jsonl` under `bench/` is scanned for an attempt-level
  `evidence` value from the solve loop (`verifier`, `diff+manager`, `manager`, `diff`, `none`). This is
  the only field that names the deciding gate directly.
* **C2 — configuration.** Every bench whose tracked results hold one row per `chimera solve` (or per
  `AutonomousAgent.run`), classified by the regime its runner configured: *Manager on / off* ×
  *executable verifier on / off*. The regime **Manager on, no verifier** is the one where the Manager
  decides every attempt. Found by grepping the runners before computing: `harness_bench` (factorial,
  `--no-manager`), `swe_bench` (baseline `--no-manager`; treatment arms Manager on, no `--verify`,
  `--keep-workspace`), `tool_defer` (Manager on — no `--no-manager` — and no `--verify`), `local_lift`
  journals, `learning_lift`, `retry_lift`, `edit_tools` (all `--verify`), `test_gate_two_sided`
  (`--no-manager`), `unattended_claims` (`use_manager=False`). The script lists the exact files and counts.
* **C3 — run level, `tool_defer`.** The one stored dataset where the Manager decided with no verifier
  **and** the run's own verdict (`exit` 0 iff `result.success`, `chimera/cli/main.py`) **and** an
  executable oracle (`completed`, the task's shell verifier, run after the solve) are all recorded. It ran
  on 2026-09-02, after the Manager started receiving the diff (#244, 2026-08-28), with the workspace guard
  on and without `--keep-workspace`, so a failed run's every attempt was reverted before the oracle ran.
  Table: run verdict × oracle, pooled over both arms (the arms differ in tool deferral, not in the gate).
* **C4 — identifiability.** For the runs the gate failed, does any stored field hold the reverted work, so
  that its correctness could be judged? (`discarded_at` lived in a temporary `CHIMERA_HOME`.) Descriptive
  only, never read as evidence: the failed runs whose final answer *claims* the oracle's expected value.
* **C5 — attempt level, the `manager_diff` corpus.** Control first (§2aa): the script must reproduce the
  published **47/246** and **2/139** from `bench/manager_diff/results/manager_diff.jsonl` and the
  corpus labels (oracle ≥ 0.8), or nothing else is read. The advisory arm is computed, not called: the
  corpus is exactly the solves the non-Manager gates let through (`--no-manager`, one attempt), so an
  advisory Manager approves all 385. Reported: TPR and FPR of both arms and their differences; on the
  262 rows whose evidence shows a productive change as well (manager_diff's blind-spot split); and how
  much a REVISE note would tell a reader, `P(false | REVISE)` against the base rate `P(false)`.

## Decision rule (fixed now)

* **D1 — advisory becomes the default** only if, on C5 (all 385), advisory − gate gives
  **ΔTPR ≥ +0.20 and ΔFPR ≤ +0.05** (the adoption bar study 28 proposed for MA2). *This one is arithmetic
  on published counts — ΔTPR = 1 − 47/246, ΔFPR = 1 − 2/139 — so its outcome is known while writing this.
  It is registered so the record says why the default does or does not move, not as a prediction.*
* **D2 — advisory ships as an opt-in setting (default off)** only if the run-level counterfactual is
  identifiable from stored data (C4) **and**, on it, advisory delivers oracle-true runs at least 5 points
  above the gate (share of all runs) while adding at most 5 points of oracle-false delivered runs — the
  same bar as D1, read where the retries and reverts actually happen.
* **Otherwise — nothing ships.** If C4 says the counterfactual cannot be recovered, RESULTS.md says the
  data cannot answer the question and names the measurement that can; no product code changes.
* C1–C3 decide nothing on their own. They answer the census question and say whether the regime is
  reached at all in stored runs.

## Prediction (written before computing)

* **C1: zero** stored rows carry an attempt `evidence` label — benches keep pass/fail and cost, and the
  receipts lived in per-run homes outside the repository.
* **C2:** the Manager-decides regime is a minority of stored solves, roughly 10–20%: `tool_defer` (60) and
  the SWE-bench treatment arms; every other bench either passes `--verify` or `--no-manager` by design.
  The SWE-bench rows cannot inform C3 — their runners stored no run verdict, `--keep-workspace` restored
  the last attempt on failure, and they predate #244 (the Manager saw prose only).
* **C3:** of the runs the gate approved, ≥ 85% pass the oracle; 30–45% of runs end with every attempt
  reverted, and all of those fail the oracle, by construction. ⚠️ **Not a blind prediction:** while
  checking what `exit` and `tail` mean in `tool_defer/results.jsonl` I tabulated verdict × oracle once
  (37 approved, of which 4 fail the oracle; 23 failed). Recorded here so the reader discounts C3 as a
  prediction; the decision rule does not rest on it.
* **C4: not identifiable.** No stored field holds a reverted attempt; the oracle ran on a reverted tree.
* **C5:** control reproduces; D1 fails (ΔFPR ≈ +0.99); a REVISE note carries little information
  (`P(false | REVISE)` within 0.10 of the base rate), while an APPROVE is strong evidence of a true success.
* **Overall:** D1 fails and D2 cannot be evaluated, so nothing ships.

## Protocol (`bench/PROTOCOL.md`)

§1 no agent runs here. §2: halts — `tool_defer` recorded `timed_out` per row; a timed-out row leaves the
C3 denominators and is counted. §3–4: no calls, no cache, no adapter. §5: the Manager is a judge whose
replay / paraphrase floor was never measured on this corpus; C5 inherits that limit from `manager_diff`.
§2m: configuration is read from the runner at the commit that wrote each result, because the artifacts
do not record it — stated as a limit, not hidden.

## What this cannot show

Production frequency: benches choose `--verify` / `--no-manager` deliberately, so stored runs say nothing
about how often a desktop or terminal run reaches the Manager path; that number lives in `runs.jsonl` on
the machines that ran them (PC, VPS), which this study does not read. The verifier-abstained case (a
`--verify` command that could not judge, handing the vote to the Manager) leaves no trace in stored rows.
`tool_defer` is one model, ten small tasks, 60 runs.
