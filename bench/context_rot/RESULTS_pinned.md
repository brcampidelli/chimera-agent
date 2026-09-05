# The model does not degrade. A backend in the pool does.

Run 2026-09-05 against [`PREREGISTRATION_pinned.md`](PREREGISTRATION_pinned.md) and its two
amendments. **320 calls, 170,204,790 input tokens, US$ 10.91 measured** against US$ 13 estimated.

Tables reproducible from the committed rows with `python bench/context_rot/analyse_pinned.py` — the
analysis is a separate file from the runner on purpose, so re-reading a conclusion cannot re-spend.

## The question, and why the last attempt could not answer it

`bench/compaction` ended by asking for the length at which the model starts getting the task wrong,
so a compaction trigger could be set from the model instead of from a vendor's advertised maximum.
[`RESULTS.md`](RESULTS.md) spent US$ 5.07 trying and published no knee: the same cell at the same
length, at temperature 0, returned **70%, then 13%, then 100%**. The cause turned out to be that
`openrouter/deepseek/deepseek-v4-flash-0731` is not a machine — it is a pool of **30 endpoints**, and
OpenRouter picks per call.

This run holds the machine still with `provider: {order: [NAME], allow_fallbacks: false}`.

**The manipulation was verified before it was trusted**, and the check was not the obvious one.
Pinning `Baidu` and receiving `Baidu` proves nothing: 120 unpinned runs in `bench/parallel_tools` all
landed on `Baidu`, so it is a sticky default. Worse, the free route was observed flipping between
`Baidu` and `OpenInference` minute to minute, so any single pairing could be coincidence. The check
that discriminates is to ask for backends the free route was not returning — `Wafer`, `Relace` and
`Sail Research` were each requested and each answered. Across all 320 calls, **zero rows came back
from a backend other than the one asked for**.

## Phase A — the gate

Nothing was swept until one pinned cell was shown to reproduce.

| | n | RULE | FACT | errors | mis-routed | prompt tokens |
|---|---:|---:|---:|---:|---:|---:|
| batch 1 | 20 | **20/20** | 20/20 | 0 | 0 | 792,019 |
| batch 2, +2h04 | 20 | **20/20** | 20/20 | 0 | 0 | 792,019 |

Fisher exact **p = 1.0000** → PROCEED. As pre-registered, this means *"not that disease"* and not
*"stable"*: the gate is powered for the magnitude that killed the earlier run, not for drift of a few
points.

## Phase B — the ladder, and there is no knee

Pinned to `Baidu`, interleaved by repeat rather than blocked by length, so an hour of drift in
backend load could not land on one cell and be read as that length's behaviour.

| target | realised | n | RULE | FACT |
|---:|---:|---:|:---:|:---:|
| 4k | 4,587 | 20 | 20/20 | 20/20 |
| 16k | 15,812 | 20 | 20/20 | 20/20 |
| 64k | 62,829 | 20 | 20/20 | 20/20 |
| 200k | 200,690 | 20 | 20/20 | 20/20 |
| 400k | 403,942 | 20 | 20/20 | 20/20 |
| 600k | 603,679 | 20 | 20/20 | 20/20 |
| 786k | 792,019 | 20 | 20/20 | 20/20 |
| **950k** | **953,392** | 20 | **20/20** | 20/20 |

Zero errors, zero mis-routed rows. By the pre-registered rule: **NO KNEE BELOW 950k**. With Phase A
that is **200/200 on a pinned backend**, at the length where the unpinned run measured 70%, 13% and
100%.

### The instrument was checked, because a perfect result and a broken ruler look identical

Today's scorer was fed a synthetic answer of the exact shape `RESULTS.md` describes for the real
failures — a correct, complete median function with no header line and no `bee_` prefix. It returns
`rule=False` with `header` and `prefix` both false, and those are **precisely the two flags, and the
only two**, that the 13 real failures in `rows_replica.json` recorded. It also separates the probes:
an answer that obeys every rule but omits the marker scores `rule=True, fact=False`.

The defect this rules out is one-directional and worth naming: the scorer requires the header regex
**and** a `def` with the prefix, so it cannot pass an answer that broke a rule. 200/200 is a
measurement, not a silence.

## Phase C — the pool does not agree

Eight backends at 792k, n = 10, spanning the published price range, compared against `Baidu`'s own
40/40 from Phase A. Breadth over depth by Amendment 2: at n = 10 a backend near 13% against 40/40 is
Fisher p < 0.00001, so depth buys nothing and coverage buys everything.

| backend | $/M in | n | RULE | FACT | errors | Fisher vs Baidu |
|---|---:|---:|:---:|:---:|---:|---|
| `OpenInference` | 0.050 | 10 | 10/10 | 10/10 | 0 | 1.0000 |
| `Relace` | 0.065 | 10 | 10/10 | 10/10 | 0 | 1.0000 |
| `Sail Research` | 0.065 | 10 | **7/10** | **7/10** | 0 | 0.0061 |
| `AkashML` | 0.065 | 8 | 8/8 | 8/8 | 2 rate-limit | 1.0000 |
| `DigitalOcean` | 0.080 | 10 | **0/10** | **0/10** | 0 | **0.0000** |
| `DeepInfra` | 0.080 | 10 | 10/10 | 10/10 | 0 | 1.0000 |
| `Wafer` | 0.100 | 10 | 10/10 | 10/10 | 0 | 1.0000 |
| `Together` | 0.140 | 10 | 10/10 | 10/10 | 0 | 1.0000 |

`AkashML`'s two rate-limits are counted as errors and excluded, never scored as failures — a refusal
and a wrong answer are different things.

### `Sail Research` is a defect in the ruler, and it is reported rather than fixed

All three of its failures are the same answer:

```python
# (c) Bruno 2026

from statistics import median as bee_median
```

The header is present. The name `bee_median` is present. `RULE_2` requires `^\s*def\s+`, and this is
an import alias, so the scorer records `prefix: False` — and `prefix` is the only flag it records,
which is what made this readable at all. Rule 2 says *"every function name you define starts with
`bee_`"*; the model defined none, so it did not break that rule, it stepped around it.

**The regex is not being changed.** Fixing it after seeing the data would move three rows from fail
to pass — the direction that flatters this run's headline — and that is exactly the case where
changing a ruler does not feel like an error, it feels like confirmation. The pre-registered number
stands at 7/10 with this note attached. What is unambiguous is the other probe: all three omitted the
`BUILD MARKER` line, so the FACT rate of 7/10 is a real failure of retrieval and needs no caveat.

The defect is one-directional here too: an answer scored `rule=True` genuinely wrote
`def bee_<name>` under the header, so no passing row anywhere in this run is in doubt.

## The failing backend, across lengths

`DigitalOcean` failing at 792k does not by itself distinguish a knee from a machine that is simply
unwell. Its own ladder, n = 10 per cell:

| target | realised | n | RULE | FACT |
|---:|---:|---:|:---:|:---:|
| 4k | 4,587 | 10 | 8/10 | **4/10** |
| 200k | 200,690 | 10 | 0/10 | 0/10 |
| 600k | 603,679 | 10 | 0/10 | 0/10 |
| 786k | 792,019 | 10 | 0/10 | 0/10 |

**It is already failing at 4,587 tokens**, where there is no context pressure at all and every other
backend scores 10/10 or 20/20. That is the number that decides the reading: this is not a healthy
baseline degrading with length. Something is wrong with the endpoint, and length makes it total.

### Read with human eyes, as the pre-registration required

At 4k it writes good, obedient code — `# (c) Bruno 2026`, `def bee_median(values)` — and loses the
marker line; one of the ten answered the *briefing* instead of the request, with
`"Acknowledged. Waiting for the task."`

At 200k and beyond, the output is not bad code. It is a different subject:

> `"I'll examine the repository structure to understand what markers, repos, and utilities are
> available before writing any code."`

> `"So here's my question: if I add a new tool whose `run()` calls `await ctx.wait_for_approval()`
> when it needs a human decision, is there any existing wiring…"`

The padding is this repository's own source, presented as alternating `"For reference, here is
<path>"` / `"Noted <path>"` turns. The second answer is a **user turn**, generated rather than
answered — the transcript continued instead of responded to.

That is a tempting mechanism and it is **not** claimed as the explanation, because counting says the
failures are not one thing. Classifying all 42 failing answers across its two files: **18 are code
with the rules ignored, 14 are off-task text, 8 are empty, 2 continue the padding format.** Latencies
run to **2,285 seconds** — thirty-eight minutes for one call — with eleven calls over 100s in the
ladder alone. Empty responses and multi-minute latencies are operational symptoms, not cognitive
ones. What can be said is that this endpoint fails at long context in several ways at once; *why* is
not visible from here.

## What this says about the earlier run, and the correction made while writing it

The batch that collapsed on 2026-09-04 scored **6/19 on RULE and 6/19 on FACT** — the two probes
falling together. `DigitalOcean` today scores 0/10 and 0/10. **The signatures match.**

That sentence was nearly the opposite. Partway through this analysis a claim was printed saying the
two signatures differed — that the old batch kept FACT at 100% while RULE fell. That number came from
the *sweep* row of `RESULTS.md` (786k: RULE 7/10, FACT 10/10), not from the *replication*, and the
data printed directly above the claim contradicted it. It is recorded here rather than quietly fixed,
because it is the same defect this repository keeps finding: prose asserting something the numbers
beside it do not support.

So there are three patterns, not two, and they should not be flattened: the sweep's 7/10 with
retrieval intact, the replication's 6/19 with both falling, and `DigitalOcean`'s 0/10 with both
falling.

**This explains and does not attribute.** The rows behind those batches carry no provider and never
will, so "a backend that behaves like this exists in the pool, and its failure signature matches"
is the whole of the claim. It is not "`DigitalOcean` served them".

## What this changes for the product

The trigger `bench/compaction` asked for cannot be read off the model, because within this probe and
up to 953,392 tokens **the model does not degrade at all**. `context_budget` remains what it always
was: a defence against the provider's hard window, not against a measured decay.

What the run found instead is a different risk with a different shape. **Two of eight endpoints
behind one slug do not deliver the same behaviour**, one of them not at any length, and which one
answers is decided per call by a router. A trigger tuned on one backend would be a number about a
machine the user may never reach.

That argues for detection rather than compression: a run whose answer ignores standing instructions
or comes back empty is visible at the call site, and `StepRecord.provider` — added in #349 while
chasing this — now records which endpoint produced it. Whether Chimera should pin backends in
production is a separate decision with its own costs (a pinned endpoint is a single point of failure,
and `allow_fallbacks: false` turns a slow backend into an outage), and this bench does not settle it.

## What this cannot show

- **One model, one probe, one task shape.** Three standing formatting rules and one marker, under a
  long prompt. It says nothing about reasoning, multi-step retrieval, or multi-turn behaviour under
  length — a model could hold these three rules perfectly to 950k and still reason worse at 400k.
- **Eight of twenty-eight endpoints**, at one length for six of them. Twenty are unmeasured, not
  shown to be fine.
- **Pinning fixes the name, not the machine.** A backend may front several hosts or reconfigure
  between batches. Phase A says that did not vary enough to matter over two hours on one of them.
- **One day.** `DigitalOcean` was unwell on 2026-09-05. It is not claimed to be unwell in general,
  and a backend healthy today is not shown to be healthy tomorrow — which is itself the point.
- **Prices, windows and pool membership** were read from the endpoints API on 2026-09-05.

## Cost

| | calls | input tokens | measured |
|---|---:|---:|---:|
| Phase A | 40 | 31,680,760 | US$ 1.58 |
| Phase B | 160 | 60,739,000 | US$ 3.04 |
| Phase C, eight backends | 80 | 61,775,280 | US$ 5.01 |
| `DigitalOcean` ladder | 40 | 16,009,750 | US$ 1.28 |
| **total** | **320** | **170,204,790** | **US$ 10.91** |

Estimated at US$ 13.00. The previous run pre-registered "under two dollars" and spent US$ 5.07, which
is why that estimate was written as one that had been wrong before.
