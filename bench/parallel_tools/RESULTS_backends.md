# The 23-point spread was not between families

Run 2026-09-04 against [`PREREGISTRATION_backends.md`](PREREGISTRATION_backends.md) and its
amendment. **Verdict: the family attribution in [`RESULTS.md`](RESULTS.md) is unsafe.**

## The measurement

One slug — `openrouter/deepseek/deepseek-v4-flash-0731` — the same thirty read-only exploration
tasks, run four times with the backend **pinned** each time.

| pinned backend | answered by | steps | multi-call | rate | 95% CI |
|---|---|---:|---:|---:|---|
| `Baidu` | Baidu | 74 | 47 | **63.5%** | [52.1, 73.6] |
| `OpenInference` | OpenInference | 76 | 21 | **27.6%** | [18.8, 38.6] |
| `Relace` | Relace | 82 | 48 | **58.5%** | [47.7, 68.6] |
| `Wafer` | Wafer | 78 | 45 | **57.7%** | [46.6, 68.0] |

**Spread: 35.9 percentage points**, between four machines serving *the same model*. The
pre-registered threshold was 10. `OpenInference`'s interval does not overlap any of the other three.

For comparison, the figure `RESULTS.md` carried forward as its lasting finding was a **23-point**
spread, and it attributed that to model families.

## What the gates said

Every arm was answered by the backend it asked for — the manipulation held, and `allow_fallbacks:
false` is why. No step carried an empty provider.

The batch **shapes** are the same vocabulary across all four: `grep + list_dir`, four `read_file`
calls at once, `glob + list_dir`. So this is not four backends doing different work at similar rates;
it is four backends doing the same work and asking for a different number of tools per turn.

```
Baidu          sizes {1:27, 2:30, 3:2, 4:13, 5:2}
OpenInference  sizes {1:55, 2:6,  3:2, 4:9,  5:4}
Relace         sizes {1:34, 2:31, 3:7, 4:10}
Wafer          sizes {1:33, 2:29, 3:1, 4:12, 5:3}
```

`OpenInference` reaches the same batch sizes as the others — it just does it far less often.

## What follows for `RESULTS.md`

Its census read 137 runs with no provider recorded, because nothing recorded one. Its headline
per-model table therefore compares pools, not models, and its carried-forward finding —

> `deepseek-v4-flash-0731` and `gemini-3.8-flash` differ by 23 percentage points in how they use a
> tool-calling API

— cannot be read as a fact about those two models, because backends of a *single* one differ by more.

`RESULTS.md` is amended rather than deleted. **Its actual verdict is untouched**: the front died on
tool latency — 4.3 ms saved per run against a 5,578 ms step — and that argument never depended on the
batching rate. A model that batched on every single turn would still be saving milliseconds against
seconds.

## One coincidence, named and not claimed

`OpenInference` measures **27.6%** here. The original census measured **23.3%** for this slug. It is
tempting to conclude the census was mostly served by OpenInference and that its number was a backend
number all along.

That is not claimable. The census has no provider field and never will, its task mix was real usage
including writes while this is read-only exploration on a five-file package, and two numbers landing
near each other is not evidence of a shared cause. Recorded because leaving it out would look like it
had not been noticed.

## What this cannot show

- **One slug, one task shape, one day.** Read-only exploration on a small workspace. Whether the same
  spread appears for writes, for long contexts, or on another model is unmeasured.
- **Four backends of thirty.** The pool for this slug has thirty endpoints; four were pinned. A wider
  or narrower spread across the rest is unknown.
- **It cannot rehabilitate the old census** — that data has no provider and cannot acquire one.
- **Backend identity is a name from the router.** Two names may be one operator; one name may front
  several machines.
- **Nothing about *why*.** A different quantisation, a different serving stack, a different sampler
  default — all would look like this and none is visible from here.

## Cost

240 short agent runs across all arms, on a model at $0.065/M in. **Under ten cents**, measured. The
observational run that produced no claim cost about four.

## The larger fact behind it

The pool for this one slug has **30 endpoints**, and they are not interchangeable:

| axis | spread across the 30 |
|---|---|
| context window | 262,144 → 1,310,720 (**5×**) |
| input price | $0.050 → $0.440 per M (**8.8×**) |
| tool-batching rate | 27.6% → 63.5% (**measured here, on four of them**) |

`chimera/providers/catalog.py` records **one** number per axis per slug. Two endpoints
(`CoreWeave`, `Reka`) serve 262,144 tokens, so a compaction trigger computed from 1,048,576 is wrong
for any call that lands there — which is a sharper version of the window correction made in #346,
where `context_k` was set from `top_provider.context_length`: that is one provider's number, not the
pool's minimum.
