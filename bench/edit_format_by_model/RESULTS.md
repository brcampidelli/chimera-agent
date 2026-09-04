# Per-model edit-format tailoring: nothing to tailor

Run 2026-09-04 against the rule fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md) and its two
amendments. 66 runs, **US$ 2.63** measured (pre-registered as "estimated under two dollars").

## Verdict: REJECT

| family | n pairs | P (patch) | W (whole) | W − P | % | 95% CI |
|---|---:|---:|---:|---:|---:|---|
| `deepseek-v4-flash-0731` | 10 | 2,351 | 2,296 | **−239** | −10.3% | [−1,032, +448] **includes zero** |
| `glm-5.3-flash` | 9 | 2,109 | 2,069 | **+256** | +12.2% | [−355, +729] **includes zero** |
| `gemini-3.8-flash` | **0** | — | — | — | — | no usable pair |

Completion tokens to a passing verifier, paired per task, bootstrapped over the per-task deltas.

**Neither family shows an effect distinguishable from zero.** The pre-registered rule would already
have rejected on the 20% margin; the intervals say something stronger, which is that there is no
effect for a margin to fall short of.

## The reading this run nearly got, and why it would have been wrong

The two medians point in **opposite directions** — DeepSeek is cheaper rewriting whole files, Z-AI is
cheaper patching — at 10.3% and 12.2%. That is exactly the shape a per-model tailoring story
predicts, and the draft of this file said so: *"the direction flips, which is what the story
predicts; the magnitude does not pay for the machinery."*

It was wrong. The per-task deltas are these:

```
deepseek   -2092  -1230  -1032  -621  -387  -91  +114  +263  +988  +3184
glm-flash   -465   -355   -263  -157  +256  +287  +337  +729  +4236
```

A median of −239 out of a spread that runs from −2,092 to +3,184 is not a direction. The bootstrap
interval was added for exactly this and is recorded as an addition rather than folded in silently:
it strengthens a reject and could not have turned one into an adopt.

## The third family contributed nothing, and the run cannot say why

`gemini-3.8-flash`: 22 runs, **0 usable pairs**.

- **11 never ran.** OpenRouter rate-limited them — `usd = 0`, `tool_calls = 0`, and a tail asking for
  a BYO key to accumulate limits.
- **11 ran and failed**, at a median of 24 tool calls and US$ 0.87 total, passing 2 of 11 — against
  19 of 19 for the two families that produced data.

That looks like a capability gap between "flash"-tier models, and this run **cannot claim it**: the
tails of the failing runs carry LiteLLM provider-error banners, so some of those failures may be the
same rate limiting arriving mid-run rather than the model losing the task. Separating them needs a
key without that limit, and the two explanations are not separable from what was collected.

## What was thrown away, and why

14 of 33 pairs voided:

| reason | rule |
|---|---|
| the verifier failed | a failing run's tokens are the cost of failing, not the cost of the format |
| no cost recorded (`exit 124`, or zero tokens) | a run whose price was never recorded cannot be in a table of prices |
| the arm never used its own tool | it did not run the condition its label claims |

**Asking for the denied tool is not a void**, and that was one of the two amendments: `tool_names` is
appended before the tool runs, and a denied tool is absent from the registry, so the name in that
list is the model reaching for a refused format — the manipulation working, not leaking. Two rows.

Voiding timeouts removes the tasks an arm could not finish in fifteen minutes, which are the hardest,
so the surviving comparison is between tasks both arms completed. Any difference that only appears
under pressure is understated here.

## The side finding, which is larger than the one under test

| family | usd for 9–10 tasks, per arm | median seconds |
|---|---:|---:|
| `deepseek-v4-flash-0731` | **0.035** | 31 |
| `glm-5.3-flash` | **0.611** | 76 |

Same tasks, same verifier, same pass rate — **17× the money and 2.5× the wall clock**. Both are
"flash"-tier by name. Nothing in this bench was designed to measure that and it is reported as an
observation rather than a result, but it is a far larger lever on cost than any edit-format choice
measured here.

## What this cannot show

- **Two families, not three**, one flash-tier model each, one repeat per cell. A screening run: it
  can find a large sign flip and cannot resolve a subtle one. The claim is "no effect **this size**",
  not "no effect".
- **Small fixtures.** `tasks.py` says it: *"a rename across four toy modules is not a rename across
  django."* Whole-file rewriting is cheapest exactly when files are small, so arm W is flattered here
  relative to a real repository — and it still did not win.
- **Only the edit format.** Nothing here is about tailoring a prompt or a tool schema per family. The
  parallel-tools census found a 23-point spread between families in tool-call batching, so families
  *do* differ; this says the difference does not reach the cost of an edit.
- **The two amendments were made mid-run**, both on facts about the instrument, both recorded in the
  pre-registration with what had been seen at the time, and neither touched the decision rule.

Reproduce with `python bench/edit_format_by_model/run.py` then `analyse.py`.
