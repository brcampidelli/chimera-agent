# Study 24 — M6 and M9, step 1: counts in the stored runs

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

## M9 — the 20k-character cut in `scrape` / `read_text`: no data here to answer it

Registered: count how often the cut is hit at all before building goal-chosen chunking.

**Across 1,442 stored traces, the web tools were called 5 times, all `http_get`.** There is no `scrape`, no `browser` and no truncation marker. The stored benches are coding tasks and almost never browse, so they cannot say how often the cut bites.

The one population where it does is **production**: the Chimera running on the VPS. Counting there is read-only, but it touches production data, so it waits for the owner's call. Until then M9 is **open for lack of data**. It is not closed as "the cut is rare", which these counts cannot show (§2q).

## Reproduce

Run in WSL, with the harness venv, where the homes live:

```bash
~/hb-venv-b4b/bin/python bench/study24_counts/m6_tool_loop.py
~/hb-venv-b4b/bin/python bench/study24_counts/m9_scrape_cut.py
```
