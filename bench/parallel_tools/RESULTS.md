# What parallel tool calls would buy: 4.3 ms per run

**Verdict: do not build it.** Measured 2026-09-04 over 137 real runs already on disk, at a cost of
nothing.

`chimera/core/agent.py` executes a turn's `tool_calls` in a plain `for`, one after another. Making
that concurrent is a genuine piece of work — a read-only marking on `Tool` that does not exist today,
thread-safety on a `TaintLedger` that has none, and a change to what the loop-breaker *is*: today its
`break` stops the remaining calls in a batch from running, and in a concurrent batch they have
already run by the time a verdict exists, so a circuit breaker becomes a report.

Before any of that, one number decides whether the front exists. Nobody had measured it, and
`tests/test_three_zero_cost_defects.py` records that every stub in the suite declares one call per
step — a hint that the shape was assumed rather than observed.

## The measurement

`StepRecord.tools` is exactly the batch, and `traces.jsonl` already held it for every run this
install has made. No new runs, no tokens spent. A **complete** census of the available traces rather
than a sample: a sampled census of co-occurrence undercounts by construction, and this one was 137
records and a second of CPU.

## The answer, and why the aggregate is the wrong number

Across all traces, 6.9% of tool-calling steps carried two or more calls. That figure is a mixture
over models that behave in opposite ways, and reporting it alone would be a claim about none of them:

| model | steps with tools | multi-call | ceiling on waits removed |
|---|---:|---:|---:|
| `gemini-3.8-flash` | 226 | **0.0%** | 0% |
| `deepseek-chat-v3.1` | 188 | **0.0%** | 0% |
| `deepseek-v4-pro` | 148 | 6.8% | 0.6% |
| `glm-5.3-flash` | 111 | 22.5% | 4.2% |
| **`deepseek-v4-flash-0731`** (the shipped default) | 60 | **23.3%** | **10.8%** |
| `glm-5.3` | 57 | 1.8% | 1.7% |
| `grok-4.6` | 16 | 31.2% | 18.2% |
| `kimi-k3` | 8 | 37.5% | 25.0% |

Two models with zero multi-call steps account for half the corpus and drag the average to a third of
what the default model does. "Ceiling" is the share of tool waits that would disappear if a batch of
N safe calls took the time of one — an upper bound that ignores dispatch and assumes equal-length
calls.

## Where the front dies: what those waits are worth

Only batches made **entirely** of read-only tools can be parallelised safely — a mixed batch races a
write and disarms the loop-breaker — and the batches that qualify are these:

```
7x  list_dir, read_file        5x  grep, grep
3x  glob, read_file            2x  list_dir, list_dir
```

Measured on this machine, on this repository:

| tool | median |
|---|---:|
| `read_file` | **0.3 ms** |
| `list_dir` | **1.0 ms** |
| `glob` | 23.7 ms |
| `grep` | 69.8 ms |
| dispatching one thread | 0.13 ms |

The most common safe batch, `list_dir + read_file`, costs 1.3 ms sequentially. Running it on two
threads saves 0.3 ms and spends 0.13 ms getting there.

Against the thing an agent loop actually waits for:

| | |
|---|---:|
| median step (model call + its tools) | **5,578 ms** |
| total saving across all 137 runs, every safe batch parallelised | **589 ms** |
| per run | **4.3 ms** |
| as a share of one median step | **0.08%** |

**Parallelising every safe tool call in a run saves less than one hundredth of a single model
call.** The waits in this loop are seconds of inference; the tool calls are milliseconds of local
filesystem. That is structural, not a property of this corpus.

## What this census could not show

- **137 runs from one install.** The task mix is what this machine happened to do, weighted towards
  short probes. A long exploratory task over a large tree would batch more reads.
- **The model list is the one that was used.** A model absent here — Claude, GPT — may batch far more
  aggressively; `kimi-k3` reaches 37.5% on eight steps, which is a hint rather than a rate.
- **`grep` at 69.8 ms is this repository on this disk.** A monorepo an order of magnitude larger
  moves that number, and `grep, grep` is the second most common safe batch.

None of those close the gap. For 4.3 ms per run to become interesting against a 5,578 ms step, the
saving would have to grow by roughly three orders of magnitude — which no plausible shift in model
mix or corpus size produces, because the ceiling is bounded by tool latency and tool latency is
milliseconds.

## What to do instead

Nothing, here. The finding worth carrying out of this is the one about the aggregate: `deepseek-v4-
flash-0731` and `gemini-3.8-flash` differ by 23 percentage points in how they use a tool-calling
API, and any measurement of loop behaviour that averages over models is averaging over that.

Reproduce with `python scripts/count_tool_batches.py`.
