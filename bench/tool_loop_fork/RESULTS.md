# M6 fork — escalating at the breaker's trip, paired at the trip: results

*2026-09-24 · **US$ 15.13** by receipt, of the US$ 20 cap · 550 solves (70 strong, 480 weak), 36 pairs · pre-registration and code committed and pushed before any solve (ecf47b1) · 2 weak cells frozen as missing after a DNS outage (§6); neither had tripped, so no pair was lost.*

## Verdict: escalating beats stopping on both executors — but on the strong one every trip was the breaker's own false alarm

| executor | pairs | trip rate | stop (snapshot) | escalate | **Δ** [95% CI over pairs] | helped / hurt / tied | extra US$ per trip |
|---|---:|---:|---:|---:|---|---|---:|
| strong `deepseek-v3.2` → `gpt-6-sol` | 20 | 0.286 | 0.449 | 0.915 | **+0.466** [+0.337, +0.592] | 15 / 1 / 4 | +0.40 |
| weak `gpt-oss-20b` → `deepseek-v3.2` | 16 | 0.033 | 0.236 | 0.509 | **+0.273** [+0.142, +0.421] | 12 / 0 / 4 | +0.05 |

**By the registered rule,** both CI lower bounds are above zero, so escalation is documented as a recommended opt-in for loop-prone work, with its cost per rescued point:
- strong: about **US$ 0.86 per point**;
- weak: about **US$ 0.19 per point**.

The flag stays **off by default**, as registered. §3 says what that recommendation is worth once the breaker is fixed.

**The implied arm-level effect is trip rate × Δ.** It is +0.133 on strong and +0.009 on weak. This is the quantity M6's design measured as +0.049 (strong) and could not resolve.

## 1. Gates, before any score was read

- **Ruler.** All 550 logs name the frozen worktree and the escalation target.
- **Contamination audit.** One flag. A strong solve's own test output printed its own workspace path, and the trace cut that path off mid-string. It is not a read of another solve's workspace.
- **Copy check.** In all 550 cells, a copy of the escalated workspace graded to the harness's published score within 0.001. A US$ 0 pre-check on M6's 72 sandboxes had already given 72 of 72. So grading the stop arm on a copy (the snapshot) measures with the same ruler as the escalate arm.

## 2. Against the predictions

| prediction | outcome |
|---|---|
| strong Δ +0.15 to +0.40, CI above zero | **refuted high**: +0.466, CI above zero |
| weak Δ +0.05 to +0.25 | **refuted slightly high**: +0.273 |
| tripped strong solve costs ~US$ 0.20 more | **refuted high**: +US$ 0.40 |
| tripped weak solve costs cents more | **confirmed**: +US$ 0.05 |
| trip rate ≥ 10% strong | **confirmed**: 28.6% |
| trip rate ≥ 4% weak | **refuted**: 3.3% |

## 3. What the breaker tripped on (descriptive, not registered — `results/trip_patterns.txt`)

The pattern is read from the four tool calls before each escalation.

**Strong: all 20 pairs are the same false alarm.** Each was four **distinct, successful** edits (`edit_file` / `apply_patch`, different args each time), answering with the same confirmation, e.g. `edited in/db/migration.sql: replaced 1 occurrence`. The breaker's no-progress rule compared the tool and the output, never the args, so it read four pieces of work as a stall.

The +0.466 is therefore mostly **not stopping a run that was working**. A stronger model picking it up is a smaller part of it. This design cannot separate the two: every escalated run continued on Sol.

**Weak:**

| pattern | pairs |
|---|---:|
| **wall** — the same error four times | **11** |
| `list_dir` with trivially different args returning the same listing | 3 |
| a spin (the same call, the same output) | 1 |
| other | 1 |

The walls have several causes:
- a tool name corrupted by the model's own chat format (`write_file<|channel|>commentary`, 5 of them);
- patches in the wrong format (4);
- a broken shell call (2).

**On the weak executor, escalation rescues runs that were genuinely stuck.**

**What followed, on the same day.** The breaker was fixed to require the same call **and** the same answer (#577). It is being re-measured against the legacy rule in `bench/tool_loop_fix`.

**Under the fix:**
- The strong pattern above no longer trips at all.
- The weak walls still do, because they are failures.
- The three `list_dir` cases would no longer trip. That is the fix's one known miss: near-identical args that are not work.

So the recommendation in the verdict is sharpest where it is least likely to be superseded: **escalating a weak executor that has hit a wall**.

## 4. Decision

- **Escalation stays opt-in, and is off by default.** Its docstring names the numbers above and the cost per point, and says that on strong the effect was measured against a breaker that stopped productive edits.
- **Whether escalation still helps strong runs once the breaker stops only real loops is unmeasured.** It would take a new registration with the fixed breaker, where strong trips should be rare.

## 5. Spend

- **By receipt:** US$ 15.13.
- **Without a receipt:** attempts cut at chunk ends, and the DNS-outage crashes. These are bounded by OpenRouter's own counter for the UTC day, read at 15:57 UTC: US$ 20.16 for M6, the fork and M7 together, against US$ 17.57 in their receipts at that time. So everything unreceipted cost at most about US$ 2.6.
- **Total:** the fork's real spend stays under US$ 20. The weak solves run after that read add cents.

## 6. Apparatus record

- **Seven 50-minute chunks.** The driver resumes; a solve cut at a chunk's end re-runs whole, with its home and log cleared first.
- **A DNS outage in WSL around 14:41** (`[Errno -3] Temporary failure in name resolution`). It crashed solves in this run and in `bench/tool_loop_fix` in the same minute. Two weak cells failed both attempts inside the window and were frozen as **missing**, as registered: 041 r6 and 082 r26. Neither had tripped, so no pair was lost. The halt it caused was investigated, not loosened.
- **Pilot:** one weak solve on `011-code-debug`, outside the registered tasks, never analysed.

## 7. What this cannot show

The registered list stands:
- the final answer's text;
- tasks outside these four;
- other escalation targets or signals;
- more than one escalation;
- retries.

Added by the outcome:
- **how much of the strong Δ came from the stronger model.** It needs an arm that continues on the **same** model after the trip. With the breaker fixed, most of these runs would not stop at all.
