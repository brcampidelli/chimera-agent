# Result — 2026-09-02

Run: `python bench/tool_defer/run_paired.py --repeats 3`, 10 tasks × 3 repeats × 2 arms = 60.
Raw rows in `results.jsonl`. The thresholds and the exclusion rule below were fixed in
`PREREGISTRATION.md` before any of this was seen.

## Primary — did the task complete

The pre-registration excludes B-arm runs that never touched the proxy: `run_shell` is in the core in
both arms, so a run can shell out to Python and pass without exercising the thing under test.
Seven did, and are reported rather than counted.

| | A (declared) | B (deferred) |
|---|---:|---:|
| completed, all 30 | 18 (60%) | 15 (50%) |
| completed, the 23 pairs where B used the proxy | 12 (52%) | 8 (35%) |
| discordant pairs | \- | 4 for A, 0 for B |

**McNemar two-sided p = 0.125 — inconclusive**, and the pre-registration said a difference this size
would be. Four discordances all one way is the shape of an effect; four is not enough of it.

## Secondary — cost

| | A | B |
|---|---:|---:|
| prompt tokens, total | 1,970,920 | 1,219,885 |
| per task **completed** | 109,495 | **81,325** (−26%) |
| steps | 352 | 394 |
| timeouts | 0 | 0 |

Per completed task, not per run: an arm that fails cheaply is not better than one that succeeds
expensively.

## What the mechanism did

B reached the proxy in **23 of 30** runs. There was no case of the agent failing to find a tool and
giving up — the failure mode the MCP deferral names as its unmeasured risk did not appear here.

## Why this cannot answer the question, and it is the bench's fault

Four of the ten tasks fail in **both** arms: `json_chart` 0/3 and `site_map` 0/3 in each,
`page_words` and `primes_sum` close behind. A bench whose tasks the model barely solves measures
their difficulty, not tool selection. Only 23 of 30 pairs exercised the mechanism and fewer still
could discriminate.

Recorded as a limitation of this apparatus rather than as a property of deferral. A second attempt
would need tasks the model completes reliably in the control arm and that cannot be reached with
`run_shell`, which is a harder corpus to author than this one was.

## Standing recommendation

`CHIMERA_DEFER_TOOLS` stays **off by default**. The token half is settled (arithmetic in
`break_even.py`, −26% measured here); the selection-accuracy half is still unmeasured — not for want
of trying, but because this instrument lacked the power to answer.
