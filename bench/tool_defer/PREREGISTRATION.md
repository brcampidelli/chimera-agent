# Deferred tools — does the agent still find them?

Committed before the harness runs. It fixes what counts as an answer so the answer cannot be
chosen after seeing the numbers.

## The question, and the one it is not

`chimera.tools.defer` moves eighteen tool schemas out of the prompt and behind
`tool_list` → `tool_describe` → `tool_call`. `chimera.integrations.mcp_defer` does the same for MCP
servers and has been **off by default since it was written**, for a reason stated in its own
docstring: the saving is in tokens, the risk is in selection accuracy, and *"a model that has to
search for a tool may pick worse than one handed the list"*. Nobody measured that half.

**The token half is already settled, on paper, and does not need a run.** The schema is re-sent on
every step, so deferral saves 1,877 tokens per step and costs whatever extra steps the lookup adds.
Weighted across turns that do and do not need a deferred tool, the break-even is at **60% of turns
needing one** in the most hostile case modelled (3 steps, history growing 800 tok/step, 2 extra
steps), and higher everywhere else. Observed use is 0 of 33 calls. Arithmetic, not opinion, and it
is in `poder2.py` alongside this file.

So this bench measures the other half, and only it:

> **With deferral on, does a turn that needs a deferred tool still complete?**

A failure here is not a slower turn. It is a turn that cannot do the thing.

## What is measured

Primary — **completed**: the task's verifier exits 0. Binary, per task, per arm.

Secondary, and neither may be read as the headline:

- **steps**: how many tool-calling steps the turn took.
- **prompt tokens**: summed across steps, from the provider's own usage.
- **reached through the proxy**: whether the turn actually called `tool_list` / `tool_describe` /
  `tool_call`.

That last one is not decoration. `run_shell` is in the core and stays declared, so an agent that
cannot find `read_document` can often shell out to Python and still pass. That is good for the
product and fatal for this measurement: a B arm that completes without ever touching the proxy has
routed around the thing under test, and its success says nothing about selection accuracy. **Those
runs are reported separately and excluded from the primary rate**, with the count printed. An
intervention that cannot say how often it actually acted cannot be read at all.

## Arms

| arm | `CHIMERA_DEFER_TOOLS` |
|---|---|
| A | unset — every tool declared, today's behaviour |
| B | `1` — core declared, eighteen behind the proxy |

Same model, same tasks, same workspace fixtures, same `max_steps`. Paired: every task runs in both
arms, and the comparison is within-task.

## Tasks

Ten, each needing a tool that arm B defers, each with a shell verifier that exits 0 or 1. Half
offline, half touching the network, because a bench made only of offline tasks would not exercise
`scrape` / `http_get` — the deferred tools an ordinary session is most likely to want.

Authored before either arm ran, and none authored by watching an arm fail.

## Repeats and what would make this inconclusive

Three repeats per task per arm — thirty runs per arm. Not two: two repeats produce a difference, not
a variance, and this project has already been caught reading one as the other.

**Declared in advance:** with n=10 tasks the smallest difference this can resolve is large. A
one-task difference (10 pp) is inside the noise of three repeats and will be reported as
inconclusive, not as a result. If both arms complete 10/10, the honest reading is *"no selection
failure at this n"* — which is a bound, not a proof, and the bound is what gets written down.

## What would change the recommendation

- **B completes as often as A, and reaches through the proxy:** the unmeasured half is measured, and
  `defer_tools` can default to on with a number behind it.
- **B completes less often:** the feature stays off and the failure mode gets named.
- **B completes as often but never reaches through the proxy:** the bench did not measure what it
  claims, and the tasks are wrong. Says so; does not report the rate.
