# Results — M6: when the tool-loop breaker trips, stop or escalate?

*Closed 2026-09-24. 72 of 72 cells (6 tasks × 2 arms × 2 executors × k = 3), **US$ 3.56** in receipts. Attempts cut at a chunk's end have no receipt; their spend is bounded in §6, and the total stays under the US$ 8 cap. Pre-registration `PREREGISTRATION.md`, committed with the flag before any solve (ab203e5, c096893), with **no amendments**. Raw data: `results/` (`summary.json` is the registered read, `cells.json` has one row per solve).*

## The verdict in one line

**A tie at this bench's power, on both executors, so the flag stays off and is not recommended; it is not removed either.**
- **Strong executor (`deepseek-v3.2` → `gpt-6-sol`):** Δ **+0.049** [−0.108, +0.208].
- **Weak executor (`gpt-oss-20b` → `deepseek-v3.2`):** the breaker never tripped in the `esc` arm, so escalation never happened. Its Δ **−0.066** is replica noise between two identical configurations.

The number worth keeping is on the strong executor and sits below the registered comparison. The three runs that escalated scored **0.726**; the three the breaker stopped scored **0.396**, on the same two tasks. Escalating cost **+US$ 0.23** per run. A design that pairs over all solves dilutes that effect by the trip rate (1 in 6), and this one could not see it.

## 1. The registered read

| executor | floor (stop replica SD) | score stop → esc | **Δ** [95% CI over tasks] | pass@0.8 stop → esc | US$/solve stop → esc |
|---|---:|---|---|---|---|
| strong | 0.244 | 0.587 → 0.636 | **+0.049** [−0.108, +0.208] | 16.7% → 22.2% | 0.0771 → 0.1179 (**+53%**) |
| weak | 0.183 | 0.335 → 0.269 | **−0.066** [−0.204, +0.068] | 11.1% → 0.0% | 0.0014 → 0.0013 |

Paired by task over all six, with each task the mean of its three replicas. The floor is measured here, on these tasks: **0.244** on strong, against the 0.178 the registration borrowed from B4b. So this design sees less than the ≥ 0.15 its power statement claimed; its reach is closer to 0.2.

## 2. What the intervention did (§2r)

| executor | breaker ended the run, `stop` | breaker tripped, `esc` | escalated, `esc` |
|---|---:|---:|---:|
| strong | 3 / 18 | 3 / 18 | **3 / 18** |
| weak | 2 / 18 | **0 / 18** | **0 / 18** |

- **On strong the mechanism acted every time it could.** Each trip in `esc` handed the run to `gpt-6-sol`, which answered 8 to 10 of the run's ~21 steps. No second trip happened.
- **On weak it never acted.** Two trips in 18 `stop` solves and none in 18 `esc` solves is what a 5.6% trip rate gives by chance (2 of 36). With nothing to act on, the weak row measures nothing about escalation.

**Negative control, free in the design.** Before the breaker trips the two arms are the same configuration, so the runs that never tripped should agree. They do:

| executor | clean runs, `stop` | clean runs, `esc` |
|---|---|---|
| strong | 0.624 · US$ 0.0863 · 34.8 steps (n = 15) | 0.617 · US$ 0.0898 · 35.3 steps (n = 15) |
| weak | 0.355 (n = 16) | 0.269 (n = 18) |

On strong they differ by 0.007 in score and 4% in cost. On weak the gap between identical configurations is 0.086, inside that executor's floor of 0.183. That gap is the size of noise any weak-executor Δ here would carry.

## 3. The reading conditioned on the trip (not registered)

Which runs trip is exchangeable between arms, because nothing differs before the trip. So comparing tripped `stop` runs with tripped `esc` runs estimates the effect of escalating on the runs it acts on. It was **not** a registered comparison, and n = 3 per side is too small for a test; it is descriptive.

| strong executor, tripped runs | task · replica: score | mean score | mean US$ |
|---|---|---:|---:|
| `stop` (the run ends) | 041 r2: 0.292 · 082 r1: 0.300 · 082 r2: 0.596 | **0.396** | 0.031 |
| `esc` (handed to Sol) | 041 r0: 0.593 · 041 r2: 0.596 · 082 r2: 0.990 | **0.726** | 0.258 |

**The arm-level Δ is this, diluted:**

  (3/18) × (0.726 − 0.396) + (15/18) × (0.617 − 0.624) = 0.055 − 0.006 = **+0.049**

A per-trip effect of about **+0.33** reaches the pairing over all solves as **+0.05**. The floor is 0.24, so a design that pairs over all solves could not have seen it at this k. The cost behaves the same way: all of the `esc` arm's +53% comes from the three escalated runs, since clean runs cost 4% more.

## 4. Against the registered predictions

| | prediction | outcome |
|---|---|---|
| P1 | Escalation helps both executors by +0.05 to +0.15, with CIs that include zero | **strong: at the bottom edge** (+0.049, CI includes zero). **Weak: untested**, because it never escalated; its −0.066 is noise between identical configurations (§2) |
| P2 | `esc` costs 20–40% more per solve on strong, little more on weak | **strong: refuted high** at +53%, all of it from the three escalated runs (US$ 0.26 against 0.03). **Weak: untested** (no escalation) |
| P3 | The breaker fires in ≥ 10% of `stop`-arm solves | **confirmed** on both: 16.7% on strong, 11.1% on weak. On weak it is barely true, and across both arms the rate is 5.6% |

## 5. Decision (the registered rule)

- **Not recommended.** The rule asks for an executor with Δ > 0 and a CI that excludes zero; neither has one.
- **Not removed.** Neither executor loses beyond its floor, and on weak the flag never ran.
- **Off by default, as registered.** Published as a tie **at this power**, not as "escalation does not help". §3 points the other way, with too little n to say so.

## 6. The apparatus record

- **Contamination audit** (`audit_contamination.py`, the registered pre-read check): run over all 73 m6 homes before any score was read. It flags one step, and it is in the **pilot** (`011-code-debug`, not a scored cell): the weak model mistyped its own sandbox path (`…/sandbox/m3-esc-weak????`). **0 flags in the 72 scored homes.** `results/contamination_audit.txt`.
- **Ruler:** all 72 logs name the frozen worktree once per attempt (`=== chimera=…/chimera-m6-ruler/chimera ===`). Every `esc` start carries its executor's escalation flag, and no `stop` start carries one.
- **Stop rules:** never fired. 0 `rc≠0`, 0 receiptless, 0 frozen cells, cap not reached.
- **Chunks:** the run was three 50-minute foreground chunks. When the time limit ended the first two, the solves still running were cut, and the driver re-ran them from scratch in the next chunk; it clears the home and log first, so a scored cell carries exactly one complete solve. A cut attempt leaves no receipt. Its spend is bounded by at most 6 in flight × 2 cut chunks × the costliest solve of the run (US$ 0.29): **at most US$ 3.52** beyond the receipts, so at most about US$ 7.1 in total.
- **Pilot:** one solve on `011-code-debug` (weak, `esc`, US$ 0.0003) before the run, to check that the flag reached the CLI. Not scored.

## 7. What this cannot show, and the design that could

The registered list stands: tasks outside these six, other escalation targets, other signals than the breaker, more than one escalation, and retries.

Added by the outcome:
- **The weak executor's answer.** It never escalated, so nothing here is evidence about escalating a weak model.
- **Whether the +0.33 per trip is real.** It rests on three runs against three.

The design that could decide it is a **fork at the trip**. Run until the breaker trips, then continue the **same** state twice, once as `stop` and once as `esc`. That pairs exactly at the point where the arms differ, and it spends the second arm only on runs that tripped. It needs code, a resumable fork point in `Agent.run`, and a spend estimate before it is proposed.

## Reproduce

In WSL, after `write_arms_m6.py` and the chunks of `run_m6.py`:

```bash
~/hb-venv-m6/bin/python audit_contamination.py
~/hb-venv-m6/bin/python read_m6.py --json results/summary.json
~/hb-venv-m6/bin/python cells_m6.py --json results/cells.json
```
