# No knee is published, because the measurement did not reproduce

> **Superseded on 2026-09-05 by [`RESULTS_pinned.md`](RESULTS_pinned.md), which answers the question
> this run could not.** Pinning the backend, the model holds every rule 200/200 from 4,587 to 953,392
> tokens — there is no knee. What varies is the pool: two of eight endpoints behind this one slug do
> not deliver the same behaviour, and one scores 0/10 at 792k while scoring 8/10 at 4,587. This
> document's diagnosis was right and its verdict stands; what it could not do was measure past it.

Run 2026-09-04 against [`PREREGISTRATION.md`](PREREGISTRATION.md). 185 model calls, 72.4M input
tokens, **US$ 5.07** measured (pre-registered as "estimated under two dollars" — see Cost).

## What the sweep said

| model | 4k | 16k | 64k | 200k | 400k | **786k** |
|---|---|---|---|---|---|---|
| `deepseek-v4-flash-0731` RULE | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | **7/10** |
| `deepseek-v4-flash-0731` FACT | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 | 10/10 |
| `glm-5.3-flash` RULE | 10/10 | 10/10 | 9/10 | 10/10 | 10/10 | 10/10 |
| `glm-5.3-flash` FACT | 10/10 | 10/10 | 9/10 | 10/10 | 10/10 | 10/10 |

Read on its own that is a clean result: the shipped default holds three rules perfectly to 400k and
drops to 70% at 786k — the exact length at which `context_budget=0.6` would finally compact — while
**retrieval stays at 100%**, which is the separation this design existed to make. A
needle-in-a-haystack probe would have reported the model healthy at the point it stopped obeying.

The three failures were all **the same rule**, and it was the **first** one stated: the header line.
The two rules given after it survived in every case.

**Do not use that table.** It did not reproduce.

## The contradiction

7/10 against 10/10 is Fisher p ≈ 0.10 — suggestive, not a finding. So the long point was replicated
with twenty more samples, and everything after that was run to explain what came back:

| batch | n | RULE | FACT | prompt tokens |
|---|---:|---:|---:|---:|
| sweep, 786k | 10 | **7/10** | 10/10 | 791,933 |
| replication | 15 | **2/15** | **2/15** | 791,951 |
| provider diagnostic | 24 | **24/24** | 24/24 | 791,951 |
| path A/B — gateway | 8 | **8/8** | 8/8 | 791,951 |
| path A/B — raw litellm | 8 | **8/8** | 8/8 | 791,951 |

The same prompt, at the same length, at **temperature 0.0**, gave 70%, 13%, and then 100% across
forty more calls. At temperature zero the variation cannot come from sampling.

The replication's failures are not broken output. Read with human eyes, they are correct, complete
median functions that ignore every rule — `def median(...)` where the brief asked for `bee_median`,
and no header line. The model did the task and forgot the instructions.

## What was ruled out

- **Sampling.** Temperature 0.0 throughout.
- **Prompt length.** 791,951 tokens in the replication and in every passing batch since. Identical.
- **A single misbehaving backend.** The 24-call diagnostic all came from `OpenInference` and all
  passed, so the failures are not one bad machine that the router keeps returning to. What the
  rotation does explain is below.
- **The code path.** Recording the provider meant calling `litellm` directly instead of
  `gateway.complete`, which changed two things at once. An interleaved A/B — alternating call by
  call, so a drift in load could not land on one arm — gives **8/8 and 8/8**. The paths are the same.

## What explains it: a slug is not a machine

Instrumenting `provider` on the gateway — the field this run found missing — answered it directly.
**Three consecutive calls to the same slug came back from three different backends:**

```
call 0  provider='Wafer'
call 1  provider='Inceptron'
call 2  provider='DigitalOcean'
```

OpenRouter routes per call. `openrouter/deepseek/deepseek-v4-flash-0731` does not name a machine; it
names a **rotating pool**, and the members of that pool do not have to agree about what happens at
790,000 tokens — which is exactly where the disagreement appeared and nowhere else.

That is not a full attribution: the two failing batches ran before the field existed, so the backend
that served them is unrecoverable. What the rotation does establish is that **the confound is real
and was present the whole time**, in every batch, including the sweep whose curve looked clean.

## The finding, which is not the one that was sought

**At ~792k tokens this model's behaviour is not a stable function of its prompt** — and the reason is
that the prompt was never being sent to the same place twice. Not a curve with a knee: a cell that
returns 100% in one hour and 13% in another, from a pool whose membership rotates per call.

**Every measurement in this repository that names a model is a measurement of a pool.** That reaches
further than this bench: `bench/edit_format_by_model` compared "families", `bench/rag` names an
embedder, and the parallel-tools census reported a 23-point spread "between model families". None of
them recorded which backend answered, because until today nothing could.

That has a direct consequence for the question the bench was built to answer. `bench/compaction`
ended by asking for the length at which the model starts getting the task wrong, so a compaction
trigger could be set from the model instead of from the vendor's advertised maximum. **A trigger set
from a single sweep at this length would have been set from noise** — this run measured 70% and, had
it stopped there, would have published a knee that forty later calls contradict.

The low end is not exempt. 4k–400k came back 10/10 in a single pass and **was never replicated**, so
"no degradation below 400k" is one observation, not an established floor.

## The gap this found in the product, now closed

`CompletionResult` recorded which **model** answered and not which **backend served it**, so a
receipt could not distinguish "this model behaved differently" from "a different machine answered".
That is the question this run spent most of its money failing to settle.

`provider` is now on `CompletionResult`, populated on the non-streaming path from the router's own
response. It is **empty on the streaming path** — the chunks do not carry it — and that is stated on
the field rather than left for a reader to infer from a value that is always blank on one route.

## One thing was edited out of the record, and it is named here

The stored rows carry no `answer_head` preview. The probe originally asked the model to echo a
dash-separated code after an equals sign, and the echoed answers in the committed JSON tripped
this repository's secret scanner — correctly, because a scanner cannot know that a string in
that shape means nothing.

The field was dropped rather than the values rewritten, and rather than an allowlist added:
editing recorded outputs is worse than losing a debugging convenience, and weakening a security
gate so one bench fixture can pass is a trade nobody would make on purpose. Nothing in this
document rests on that field — the analysis never read it, and the three failing answers quoted
above were read before it went. The marker in `run.py` is now words rather than a code.

## Cost

185 calls, 72,381,094 input tokens, **US$ 5.07** — against "estimated under two dollars"
pre-registered. The estimate was for the 120-call sweep; the 65 calls that followed were the
investigation the contradiction required, and they were worth more than the sweep. Reported as
measured.

## What would answer the question properly

Repeated sampling of the same cell **across days**, not within an afternoon, with the provider
recorded on every call from the start. The variance to characterise is between sessions, and a
design that samples one afternoon cannot see it — which is what this run learned the expensive way.
