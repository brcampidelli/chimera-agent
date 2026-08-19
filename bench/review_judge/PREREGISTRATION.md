# Pre-registration — can Chimera's judge tell a real review comment from a plausible one?

**Written and committed before the first model call.** Everything below is fixed in advance: the
sample, the metric, the baseline, the decision rule, and what would make the result uninformative.

## Why this bench exists

`chimera/fusion/engine.py` runs a **judge** on every fused turn: it reads the panel's independent
answers and reports where they agree, where they contradict, and what each one missed. The
synthesiser writes the final answer from that analysis, so the judge is upstream of everything a
fused turn says.

This repository has **17 benchmark suites**. Grepping them for the judge finds it only as an
instrument — "judged by the same verify command", "completion tokens judged the same way". **None of
them measures the judge itself.** Every number this project publishes about fusion assumes the judge
is good at its job, and that assumption has never been tested against ground truth.

## The instrument

[`Alibaba-Aone/aacr-bench`](https://huggingface.co/datasets/Alibaba-Aone/aacr-bench) — Apache-2.0,
2145 code-review comments on 200 real pull requests across 10 languages, each labelled by senior
engineers: **1505 correct** and **640 incorrect**. The incorrect ones are not synthetic; they are
false positives produced by frontier models (Gemini-3-Pro, Claude-4.5-Sonnet and others, named per
row in `source_model`).

External, and that is the point. Three attempts at authoring our own suite in this repository failed
to land a control anywhere near an informative band — `bench/learning_lift/RESULTS.md` records
controls at 84–92% across seven pre-registered runs. A suite we did not write cannot be tuned, even
unconsciously, to the answer we hope for.

## Scope, and what is deliberately out of it

The dataset labels every row with the context a reviewer needs to judge it: `Diff Level` (1017),
`File Level` (744), `Repo Level` (384).

**This bench uses `Diff Level` only.** A judge shown a diff cannot fairly be scored on a comment
whose correctness depends on the rest of the file or on repository-wide knowledge it was never given
— that would measure the harness, not the judge, and it would measure it as worse than it is. The
other two slices are a separate question (how much context to fetch) and are not answered here.

Diff Level: **1017 rows — 754 correct, 263 incorrect (74.1% positive).**

## Sample

**A stratified pilot of n=120**, drawn from the Diff Level slice with a fixed seed (`20260819`):

- 60 correct comments and 60 incorrect ones — deliberately **not** the natural 74/26 ratio.
- The metric of interest is how well the judge catches BAD comments, and the natural ratio gives
  only ~31 of them in 120 draws. A balanced draw buys usable precision on the half that matters at
  the same cost, and the prevalence correction is stated in "Reading the result" below.
- Stratified within each label by `source_model`, so one model's failure mode cannot dominate.

This pilot is **not** the confirmatory run. It fixes the operating point and measures the cost per
item; a full-slice run (n=1017) will be pre-registered separately, citing this document, with the
threshold this pilot produces. Choosing a threshold and then reporting the same run's score at that
threshold is fitting, not measuring.

## The task put to the judge

For each row, the judge is shown the file path, the unified diff of that file from the PR, the line
range the comment is attached to, and the comment itself. It answers one question — *is this comment
a real defect in this diff?* — with a verdict and a one-line reason.

Prompt, model and decoding are fixed for the whole run and recorded in `results/manifest.json`. The
judge model is the one this install would actually use (`CHIMERA_FUSION_JUDGE`), because measuring a
model we do not ship would answer a question nobody asked.

## The baseline that must be beaten, and the trap

**A judge that approves everything scores 74.1% accuracy on this slice, and 50% on the balanced
pilot sample.** Accuracy is therefore not the metric; it is the trap. A "94% accurate" judge that
never rejects anything is worthless to fusion, and on the natural distribution it would look
excellent.

**Primary metric: rejection recall** — the fraction of the 60 incorrect comments the judge rejects.

**Cost constraint, fixed here: false-rejection rate ≤ 20%** — of the 60 correct comments, no more
than 12 may be rejected. This direction is asymmetric on purpose and follows the asymmetry the OCR
authors state outright: keeping a wrong comment costs a reader a few seconds; removing a right one
destroys a real finding silently.

**Secondary, reported always:** rejection rate overall (a judge that rejects everything is caught by
this), per-`source_model` breakdown, per-language breakdown, tokens and USD per item, and the count
of items where the judge answered in a shape the parser could not read.

## Decision rule, fixed in advance

| outcome | reading |
|---|---|
| recall ≥ 60% at ≤ 20% false rejection | the judge discriminates; proceed to the confirmatory run |
| recall 30–60% at ≤ 20% | weak but real; report as such, no product claim |
| recall < 30%, or false rejection > 20% | the judge does not discriminate on this task — publish it |

**A negative result is published either way.** `skills/publish-the-run-that-did-not-work` is a card
in this repository; a bench that only reports when it flatters the product is not a bench.

## What would make this uninformative, declared now

- **Unparseable answers > 10%** of items — then the harness is being measured, not the judge.
- **Rejection rate at either extreme** (≈0% or ≈100%) — a judge that answers the same thing to
  everything has no operating point, and its recall is an artefact of that constant.
- **A diff that could not be fetched** for a sampled row: the item is dropped and counted in the
  report, never silently replaced with another draw.

## Confidence intervals

Wilson bounds on each proportion, via `chimera/eval/anytime.py` — the same code the other suites use.
With 60 per arm the interval is wide (±~12pp), which is why this is a pilot and why no product claim
follows from it alone.

## Cost

~120 model calls, each a diff plus a short comment. Measured and reported per item; the projected
cost of the full 1017-row run is a deliverable of this pilot rather than a guess made now.
