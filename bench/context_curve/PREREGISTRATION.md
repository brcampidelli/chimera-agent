# Pre-registration — does success fall as context grows, on our own runs?

**Registered 2026-08-13, before any data existed.** That is not a formality here: at the time of
writing, the join this analysis needs returns **zero rows** (see "Starting state" below). The
criterion is fixed now precisely because nobody can yet see the number it will be applied to.

## The question

The literature calls it *context rot*: a model's reliability degrades as the prompt grows, and the
degradation begins well before the advertised window is full. It is the stated reason to build
context compaction into an agent loop — which is a real piece of work, with a real failure mode of
its own (a compaction that drops a constraint is how an agent forgets the instruction it was given).

Before building it, this project measures whether the effect is present **in its own runs**, with its
own loop, its own models and its own task mix. A number measured on someone else's harness is not
evidence about ours.

## Design

- **Unit:** one attempt. One attempt is one agent run, which is one trace line — so a run with three
  attempts contributes three points, each with the context *that attempt* carried.
- **Exposure:** `context_peak_tokens` from `traces.jsonl` — the largest prompt that attempt sent.
  The peak, not the mean: the question is whether a large context hurts, and an attempt that spent
  one call at 100k tokens was exposed to that.
- **Outcome:** `success` on the attempt receipt in `runs.jsonl`. The attempt's own flag, never the
  run's: the run's is the last attempt's, and attributing a late success to an early attempt's
  context smears exactly the relationship being measured.
- **Join key:** `run_id`, present on both sides. Unjoinable rows on either side are **counted and
  reported**, never dropped silently.
- **Buckets (fixed now):** 0–8k, 8k–32k, 32k–128k, 128k+ prompt tokens.
- **Interval:** Wilson 95%, which stays inside [0,1] at small n.

## Floors — registered, and enforced in code

| floor | value | why |
|---|---|---|
| per bucket | **20 attempts** | below this the rate moves >5pp on one flipped outcome; the interval spans most of the unit interval |
| total | **60 joined attempts** | fewer cannot populate three buckets |
| buckets above floor | **3** | with two, "declining" is indistinguishable from "one bucket is unlucky" |

Below any floor the analysis reports **"not enough data"** and makes no claim in either direction.
That is not the same as a null result, and the code says so in those words: a null result means the
effect was looked for and not found; this means nobody has looked yet.

## Decision rule — fixed before the data

- **Intervals disjoint, later bucket lower** → the effect is present on our runs. Compaction becomes
  a funded item, and this file becomes its baseline.
- **Intervals overlap** → on our data compaction is not the lever. It stays unbuilt, and the finding
  is published as a reason not to build something.
- **Intervals disjoint, later bucket HIGHER** → the opposite of the expected effect. No action either
  way until it is understood; a surprise this size usually means the apparatus, not the phenomenon.

No re-bucketing after seeing the numbers. No dropping the smallest bucket because it is noisy. If the
buckets turn out to be wrong, that is a finding to publish and a **new** registration, not an edit to
this one.

## Starting state (measured 2026-08-13)

- `traces.jsonl` — **does not exist** on the development machine, and no `*trace*` file exists in
  `/opt/data` on the production VPS either. The step log has been written at 3 of ~15 construction
  sites since it was added, and none of them was a path that runs unattended.
- `.chimera/runs.jsonl` — 403 runs, 706 attempts, all written before the receipt carried
  `prompt_tokens`, and none carrying a `run_id`.
- **Joinable attempts today: 0.**

So this analysis currently returns "not enough data", and that is its honest first output. What
changes it is time: the cron path started writing traces, and both sides now carry the key. Re-run
the command below once the logs have accumulated.

```bash
chimera context-curve
```
