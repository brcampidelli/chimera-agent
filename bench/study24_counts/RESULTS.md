# Study 24 — M6 and M9, step 1: counts in the stored runs and, for M9, in production

*2026-09-23 · US$ 0 · read-only over the harness homes in WSL (`~/hb-homes`, the B4, B4b and factorial solves) · the
first step and the stopping rule of each were registered in `bench/PLAN-study24-jev-practice.md` (#553) before these
counts were taken.*

## M6 — the tool-loop breaker: 6.4% of solves, above the 5% line — the paired arm is the next step

Registered: count `stop_reason="tool_loop"` in the stored results; below 5% of solves, M6 closes.

| | solves | share | mean oracle score |
|---|---:|---:|---:|
| ended normally (`final`) | 1,288 | 93.5% | 0.681 |
| **ended by the loop breaker** | **88** | **6.4%** | **0.416** |
| trace cut mid-write (a killed solve; counted, not hidden) | 1 | 0.1% | — |

Across 1,377 solves with a trace, the breaker fires in **6.4%**, above the line. By arm:

| family | sol | strong | weak |
|---|---:|---:|---:|
| B4 control (`off`) | 1.4% | 5.8% | 5.8% |
| B4b control (`off`) | 4.3% | 8.8% | 4.3% |
| factorial (all arms) | — | 6.8% overall | — |

**Where it concentrates:**
- **By task:** `041-frontend-state-bug` alone is 16 of the 88; then `042`, `043` and `082`, 8 each.
- **By executor:** the strong executor (Sol) rarely loops; the cheaper ones do.

**The score gap (0.681 against 0.416) is not an effect of the breaker.** The breaker fires on the harder tasks, so the gap mixes task difficulty with whatever stopping costs. Only the paired arm separates the two: the same task and replica, **stop** as today against **escalate to a stronger model**.

That arm needs code (an escalation path on the breaker) and paid solves. It is proposed, not run:
- **Tasks:** the six tasks where the breaker fires most.
- **Executors:** strong and weak.
- **Replicas:** k = 3.
- **Size and cost:** ≈ 72 solves, **≈ US$ 5**.

*Run afterwards as `bench/tool_loop_escalation` (#571): a tie at that power. The flag stays off.*

## M9 — the 20k-character cut in `scrape` / `read_text`: zero cuts in production, and almost no use — closed

Registered: count how often the cut is hit at all before building goal-chosen chunking.

**In the benches.** Across 1,442 stored traces, the web tools were called 5 times, all `http_get`. There is no `scrape`, no `browser` and no truncation marker. The stored benches are coding tasks and almost never browse, so they cannot say how often the cut bites.

**In production** (read on 2026-09-24 with the owner's go-ahead; read-only, inside the container, `m9_production.py`). The window is the scheduler's step log on the VPS: **2026-08-14 → 2026-09-24, 393 cron runs, 1,380 tool calls**.

| tool | calls | original length p50 / max | over 20,000 | cut marker |
|---|---:|---|---:|---:|
| `http_get` | 215 | 6,229 / 6,319 | **0** | **0** |
| `scrape` | **1** | 272 | 0 | 0 |
| `browser` | **1** | 135 | 0 | 0 |
| `web_search` | 2 | 64 | 0 | 0 |
| `crawl`, `extract` | 0 | — | — | — |
| *`run_shell` (positive control)* | 986 | 40 / 20,044 | **16** | **16** |

**Why a zero here is a reading and not a blind spot (§2q).**
- The step log clips each observation to head + tail (800 characters) and writes `…[N chars elided]…` in the middle, so the original length is recoverable.
- Each tool writes its own cut marker, `[truncated, N chars total]`, at the end, which is the part the clip keeps.
- The same file holds a positive control. `run_shell` shares the 20k cap; it fired 16 times, and all 16 show both the length and the marker.

A cut on a web tool would have been visible. None happened.

I first read these observations as plain slices with no length — I had looked only at their last characters — and nearly recorded "production cannot show the cut". Reading `steplog.clip` corrected it before anything was written.

**Decision, by the registered step:** goal-chosen chunking is **not built**. In six weeks of production the tools it would change (`scrape`, `browser` `read_text`) were called twice, on pages of 272 and 135 characters, and the one web tool in real use never came near the cap.

**What this cannot show:**
- **The Discord chat.** The container records nothing tool-level for it: `usage.jsonl` holds only `cron` sessions, 394 of them.
- **The desktop app.**
- **A future job that browses.**

**Seen on the way, and not M9's question.** `run_shell`'s own 20k cap cuts **1.6%** of shell calls in production:
- 12 in the daily support triage;
- 4 in the newsletter nurture job.

The cut keeps the head, so a job that needs the end of its output would not see it. Whether those two jobs lose anything was not measured.

## Reproduce

Run in WSL, with the harness venv, where the homes live:

```bash
~/hb-venv-b4b/bin/python bench/study24_counts/m6_tool_loop.py
~/hb-venv-b4b/bin/python bench/study24_counts/m9_scrape_cut.py
```

M9 in production, from a machine with SSH to the VPS (the script reads only the step log and prints counts):

```bash
ssh <vps> 'docker exec -i chimera python3 -' < bench/study24_counts/m9_production.py
```
