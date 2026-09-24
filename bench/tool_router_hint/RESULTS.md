# Results — B4b: the router as a hint, never a gate

*Closed 2026-09-23.*

- **Cells:** 412 of 414 scored (23 tasks × 2 arms × 3 executors × k = 3); 2 frozen as missing (amendment 4).
- **Spend:** **US$ 38.08** in the scored solves, plus **US$ 2.60** in a chunk that was run without the graders' tools and quarantined whole (amendment 6).
- **Pre-registration:** `PREREGISTRATION.md`, committed before any scored solve, with amendments 1–6, each committed before the data it could have been bent to fit.
- **Raw data:** `results/summary.json`; the reader's output is in `results/read-frozen-missing.txt` (primary) and `results/read-frozen-scored.txt` (the same with the two frozen cells scored).
- **Ruler:** the arms ran from a frozen worktree at `e5bbc845`, and every solve logged the Chimera path it imported (amendment 2).

## The verdict in one line

**A tie on every executor, as predicted.** The damage B4 measured came from the router's power to stop the loop and to remove tools. Take both away and it neither helps nor hurts the score. **The question closes: the tool router never as a gate; as a hint, no effect. The flag stays opt-in; no product default changes.**

## 1. The table

| executor | floor (replica SD) | score off → on | **Δ score** [95% CI over tasks] | pass@0.8 off → on | Δ cost / solve | Δ steps | router's share of cost |
|---|---:|---|---|---|---:|---:|---:|
| weak `gpt-oss-20b` | 0.129 | 0.248 → 0.310 | **+0.063** [−0.005, +0.136] | 0% → 0% | +0.0006 (n.s.) | −0.17 (n.s.) | **18.0%** |
| strong `deepseek-v3.2` | 0.178 | 0.693 → 0.696 | **+0.003** [−0.084, +0.089] | 39% → 39% | **−0.0097** [−0.0175, −0.0024] | **−3.8** [−6.9, −0.9] | 4.1% |
| Sol `gpt-6-sol` | 0.026 | 0.861 → 0.869 | **+0.008** [−0.007, +0.027] | 74% → 78% | +0.0050 (n.s.) | +0.36 (n.s.) | 0.5% |

- **Pairing:** by task over all 23.
- **Floor:** the `off` arm's own replica-to-replica SD, measured in this run.
- **Cost:** the executor's receipts. The router's own spend is outside them and shown in the last column.
- **Frozen cells as missing (the rule):** with the two frozen cells missing, strong reads +0.003 [−0.084, +0.089].
- **Frozen cells scored:** with them scored from their receiptless attempt, strong reads +0.004 [−0.086, +0.093]. The other executors are unchanged; neither cell was theirs.

**The floor moved.** On strong the control arm scored 0.693 with a replica SD of 0.178; in B4, two days earlier on the same tasks and the same model id, it scored 0.776 with 0.068. That is why the registration re-measured the `off` arm instead of importing B4's (study 21 §2ae: the route is part of the ruler). It also means this run's strong executor could not have resolved an effect smaller than ~0.18 per task. The CI above is the honest width, and it is wide.

## 2. What the router did (§2r — an intervention reports how much it acted)

| executor | router calls | hinted | **followed** | narrowed | `ANSWER` | fallbacks |
|---|---:|---:|---:|---:|---:|---:|
| weak | 450 | 447 | 141 (**31.5%**) | 0 | 0 | 3 |
| strong | 1,652 | 1,644 | 595 (**36.2%**) | 0 | 0 | 8 |
| Sol | 870 | 866 | 268 (**30.9%**) | 0 | 0 | 4 |

- **Positive control:** every `on` solve was hinted.
- **Negative control:** **0** control solves carry a router receipt, on all three executors.
- **Narrowing and `ANSWER` are 0:** the hint mode never removed a tool and never stopped the loop, by construction (#550's tests), and now by measurement.
- **The picks are B4's:** `read_file`, `run_shell`, `write_file`, `edit_file` first.

## 3. Against the registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | score ties inside the floor on every executor | **confirmed** — \|Δ\| = 0.003 / 0.063 / 0.008 against floors of 0.178 / 0.129 / 0.026, and every CI includes zero |
| P2 | the executor follows the hint on more than half the hinted steps | **refuted** — about a third, 31–36% on all three. The router names what a person would, and two times in three the executor does something else. A hint that is ignored two-thirds of the time cannot move a score much in either direction, which is part of why P1 held |
| P3 | no saving in steps or cost beyond the floor | **confirmed on weak and Sol, not on strong.** Strong's steps (−3.8) and receipted cost (−0.0097 per solve, ≈ −0.0078 once the router's own ≈ 0.0019 per solve is added back) are lower with CIs that exclude zero. On weak the router is 18% of the arm's spend, so the hint mode costs more there |

**The strong-executor saving is read as secondary, not as a finding:**
- the registration made score the primary outcome and cost and steps secondary;
- six secondary intervals were drawn (2 metrics × 3 executors), so one excluding zero is not surprising by itself;
- steps and cost are one event read twice, so they do not corroborate each other;
- the score did not move, so this is not B4's "saving = work not done" in reverse.

If someone wants it, the test is a strong-only replication with the saving pre-registered as primary. This run does not license "hints make `deepseek-v3.2` cheaper".

## 4. Decision (the registered rule)

- **Not recommended for any executor.** The rule required a score gain above the floor with its CI excluding zero on at least one executor, and no loss elsewhere; no executor has the gain.
- **A tie closes the question**, as registered. Study 22's §5 row "Tool router" is closed: *never as a gate (B4: −0.09 / −0.19 / −0.31); as a hint, no effect (B4b: +0.06 / +0.00 / +0.01, all inside the floor).*
- **The flag stays opt-in; no default changes.** `tool_router.py` keeps both modes for reproducibility.
- **A latent cache defect of the hint mode (found by study 24, item A3) is not worth fixing for a mode nobody should turn on.** The defect: the hint is a trailing `system` message, which LiteLLM hoists to the top on an Anthropic route, breaking the prompt cache. It is recorded here and in the module's docstring instead. No executor of this run was Anthropic, so the numbers above are unaffected.

## 5. Integrity checks read before the score

- **Cross-run contamination (study 24, M1).** `audit_contamination.py` read the steps of all 412 solves' traces for any path into another solve's workspace, another home, the results tree or the quarantine.
  - It flagged 7 steps. All 7 are artefacts of the trace's own argument elision: the path is cut right after `sandbox/s…`, and the text after the cut names the solve's *own* arm and timestamp.
  - **No cross-run read was found.**
  - Blind spot, stated: traces elide long arguments, so a read hidden inside an elided argument would be missed.
- **Chunk 3 quarantine (amendment 6).** One chunk ran from a non-login shell without `pytest` and `node` on PATH. Every cell it touched, 21 graded and 4 frozen, was moved out with its receipts and re-run under a PATH guard. The re-run cells include the two `016`/`084` cells that had failed for lack of tools; they then scored normally (e.g. `016` Sol r1: 1.0).
- **The ruler.** Every solve's log names the frozen worktree's Chimera. The three driver files in this folder are byte-identical to the ones that ran.

## 6. What this cannot show

- hint content other than one tool name;
- a deep router that reads the whole transcript;
- retries (`--max-attempts 1`);
- tasks outside the 23;
- a strong-executor effect smaller than its measured floor of ~0.18.
