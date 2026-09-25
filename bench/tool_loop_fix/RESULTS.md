# The loop breaker, legacy against fixed, on coding tasks: results

*2026-09-24 · **US$ 5.76** by receipt, of the US$ 10 cap · 151 of 160 solves, 9 missing after two DNS outages (§4) · pre-registration committed and pushed before any scored solve (c453334) · every log names its own arm's frozen tree, and the two trees differ only in `chimera/core/tool_loop.py` (checked).*

## Verdict: the fix passes its registered rule — on the strong executor it raises the score by +0.147, with no stronger model

| executor | legacy | fixed | **Δ** [95% CI] | floor (legacy SD) | runs ended by the breaker | US$ / solve | steps |
|---|---:|---:|---|---:|---|---|---|
| strong `deepseek-v3.2` | 0.569 | **0.715** | **+0.147** [+0.054, +0.240] | 0.253 | 11/39 (28%) → **0/37** | 0.065 → 0.082 | 28.3 → 32.6 |
| weak `gpt-oss-20b` | 0.467 | 0.529 | +0.063 [−0.042, +0.168] | 0.236 | 6/37 (16%) → 2/38 (5%) | 0.0031 → 0.0023 | 10.1 → 9.1 |

Each task weighs the same. The bootstrap resamples solves within each (task, arm) cell.

**Per task:**

| task | strong legacy → fixed | weak legacy → fixed |
|---|---|---|
| 041 | 0.576 → 0.640 | 0.609 → 0.774 |
| 042 | 0.604 → 0.740 | 0.335 → 0.271 |
| 043 | 0.342 → 0.483 | 0.170 → 0.150 |
| 082 | 0.752 → 0.996 | 0.752 → 0.921 |

**The registered merge rule:**

| condition | outcome |
|---|---|
| neither executor's CI low below −0.05 | **holds**: +0.054 and −0.042 |
| the strong breaker share falls | **holds**: 28% → 0% |
| no fixed stop left in the successful *distinct args · same output* pattern | **holds**: both fixed stops are walls (below) |

**So #577 merges** once the owner says so. The gain above zero on strong is reported as the fix's value, as registered.

## 1. What the breaker stopped

The pattern is rebuilt from the trace, because the stop path logs its reason at DEBUG only.

**Legacy, strong: all 11 stops were `edit_file` or `apply_patch` with distinct args and the same output.** That is the fork's false alarm again, on a different sample: four successful, different edits, read as a stall.

**Legacy, weak: 6 stops.** Distinct-args patches, a same-args patch loop, a `run_shell` repeat, and one mixed read/patch run.

**Fixed: 2 stops, both on weak, both real walls:**
- `run_shell` four times with the argument named `cmd` instead of `command`, each answering `error: tool 'run_shell' failed: 'command'`;
- a read/write ping-pong of errors: `file not found`, then `refused to overwrite … invalid syntax`, twice.

This is the fixed rule's intended behaviour: the same failure stops the run whatever the args, and successful work does not.

## 2. Against the predictions

| prediction | outcome |
|---|---|
| strong: breaker ≥ 15% of legacy, ≤ 5% of fixed | **confirmed**: 28% → 0% |
| strong: score +0.05 to +0.15 | **confirmed**: +0.147 |
| weak: about the same breaker share in both arms | **refuted**: 16% → 5% (legacy's weak stops included patch and shell repeats the fix no longer counts) |
| weak: Δ within ±0.05 | **refuted, slightly**: +0.063, CI including zero |
| fixed strong solves cost +20% to +60% | **confirmed**: +25% |
| legacy strong stops mostly *distinct args · same output* | **confirmed**: 11 of 11 |

## 3. What it says about the fork

The fork (`bench/tool_loop_fork`, #578) found +0.466 per trip for escalating to Sol over stopping, on strong. Every one of those trips was this false alarm.

This run answers part of what the fork could not. **Just not stopping, on the same model, is worth +0.147 at the arm level.** The fork's escalation implied +0.133 at the arm level with a 28.6% trip rate. The two figures come from different samples and different designs, so they are not a pairwise comparison. They do say that on strong, most of what escalation bought was not-stopping. Now the fixed breaker does that for free.

## 4. Apparatus record

**Two DNS outages in WSL** (`[Errno -3] Temporary failure in name resolution`), around 14:41 and 16:47–16:57.
- Each crashed the solves in flight; some cells failed both attempts inside the window.
- **Nine cells were frozen as missing, as registered:** 4 legacy and 5 fixed, across both executors. The outage does not depend on the arm or the outcome, so they cost n, not bias.
- The heaviest-hit cell is weak legacy 041, with 7 of 10 left.
- Each halt was investigated, not loosened.

**A driver overlap, caused by the operator.** After the first halt I relaunched while the old driver was still finishing its in-flight solves. The new driver resubmitted six of them, and it clears a cell's home and log before running it.
- All the processes were stopped.
- None of the six had a receipt, so all re-ran from scratch.
- No record from the overlap is in the analysis.
- The chunk scripts now refuse to start while a driver runs.

**Contamination audit:** two flags. Both are strings that cite the solve's **own** sandbox path (checked); neither is a foreign read.

**Pilot:** one weak solve per arm, on `011-code-debug`, outside the registered tasks, never analysed.

## 5. What this cannot show

The registered list stands:
- tasks other than these four;
- surfaces other than `solve`;
- how much of the fork's per-trip gain came from Sol.

Added by the outcome:
- **the fix's one known miss**, seen in the fork: near-identical args that are not work, such as `list_dir` of `.` and `./` returning the same listing. The fixed rule no longer counts them as a loop, and `max_steps` is then what bounds them.
