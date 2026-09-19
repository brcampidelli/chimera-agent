# Pre-registration — a probability on "is this review finding real?", on the aacr-bench rows

Registered 2026-09-19, before the first request. Study 20 item **B2**: the one corpus this repository
holds that is large enough for a calibration number on **human** labels — `bench/review_judge`'s
Diff-Level slice of aacr-bench (Apache-2.0; 1,017 comments labelled by senior engineers, 263 incorrect
findings and 754 correct), with the diffs already cached for the rows that have one. Five rubric arms of
a reasoning judge were measured there: A caught 15.1% of the incorrect findings at 0% false rejection,
C caught 60.4% at 38.5% — "a real axis at an unusable operating point". The question here is whether a
**probability plus a threshold read off a ROC** yields an operating point that a one-word verdict could
not, and whether a typed-decision model or a verbalized frontier model is calibrated on this decision.

## Items — the instrument that exists

`bench/review_judge/run_judge.py`: `everything(load_rows())` (Diff Level, dataset order, the pilot's 105
marked `in_pilot`), `attach_patches` (the 60-line window around the commented lines, from the cached
diffs; rows without a diff are dropped, as the judge bench drops them), and the judge's own user text —
file, lines, the diff window, the comment — as the state. Nothing is re-rendered. The label is the
dataset's: 1 = a correct comment, 0 = a false finding.

## Arms

- **J** — `typesafe/jev-1.13` through OpenRouter's Decisions API, one request per item: a **Noul**
  `real_defect` ("Does this review comment report a real defect that this diff introduces? A true
  statement is not a finding; a suggestion is not evidence that its absence is a defect; praise,
  paraphrase and style preferences are not defects; what was already there before the diff is not
  introduced by it" — the split rubric's grounds, as criteria) and a **Choice** `verdict` over
  approve / reject with arm C's two grounds as the criteria. `p` = P(real defect) = P(correct comment).
- **V** — `deepseek-v4-flash-0731`, arm C's split rubric as the system prompt with one more key in the
  JSON — `"p_real_defect": <0..1>` — and the over-confidence advisory; thinking off, `max_tokens` 2,000,
  one re-ask on empty; temperature 0.3. `p` = the verbalized number.
- **A / C** — not re-run (the judge was `deepseek-r1`, US$ 0.38 per 105 items and 100 minutes); their
  published pilot numbers are the reference the report prints beside J and V on the same 105 rows.

One repetition per arm (J is deterministic across repetitions in the previous run: std 0.005; V's
replay was 0.93 with thinking off). Cost: J ≈ 1,017 × ~1,500 tokens ≈ US$ 0.06; V ≈ US$ 0.3–0.5.

## Metrics (all with n, Wilson on rates, and the pilot rows reported apart from the 912 unseen ones)

1. **Discrimination:** AUROC of `p` against the label, on the unseen rows, the pilot rows, and all;
   by source model; and **leave-one-repository-out** AUROC as the leakage check (a comment's
   neighbours on the same PR share the diff).
2. **Operating points:** rejection = `p < τ`. **Recall of rejection** (incorrect findings rejected) and
   **false rejection** (correct findings rejected) at τ = 0.5, and at the ROC points with false
   rejection ≤ 0.10 and ≤ 0.20 — the two bounds `bench/review_judge` registered — with τ read on the
   pilot rows and applied to the unseen rows (a threshold chosen on the rows it is scored on is a fit).
   The Choice / JSON verdict scored the same way, for comparison with arms A and C.
3. **Calibration:** Brier (primary); ECE with 10 equal-mass bins beside its simulated floor (n ≈ 900,
   floor expected ~0.02–0.03 — legible for the first time in this series); reliability table.
4. **Cost and latency** per request and per arm; input tokens.

## Predictions (written before running)

- The decision is hard for everyone: **AUROC 0.65–0.75** for J and for V on the unseen rows, within
  each other's intervals; leave-one-repo-out within 0.03 of the pooled AUROC (no repo leakage — the
  label is about the comment, not the repo).
- **At false rejection ≤ 0.20, recall of rejection 0.30–0.50** for both — a usable operating point
  between A (15.1% / 0%) and C (60.4% / 38.5%), which is the thing arm C could not offer. At ≤ 0.10,
  recall 0.15–0.35.
- **Calibration:** V's Brier ≤ J's (the previous run's pattern); J over-confident in the middle bins
  again; ECE for both 0.05–0.12 against a floor of ~0.03 — i.e. the first legible mis-calibration in
  this series, on the vendor arm.
- **Source model stratum:** the incorrect findings of the weakest source model are the easiest to
  reject (AUROC highest there); the frontier models' incorrect findings the hardest.
- **Cost:** J ≤ US$ 0.10; V ≤ US$ 0.60; J latency p50 ≤ 0.6 s (longer states than the governance run).

## Decision rule, written before the numbers

The arm earns a line in `bench/review_judge/RESULTS.md` as "an operating point exists" only if, on the
**912 unseen rows** with τ chosen on the 105 pilot rows: recall of rejection ≥ 0.40 at false rejection
≤ 0.20, with the recall's Wilson lower bound ≥ 0.30. Otherwise the reading is "the probability ranks a
little and buys no usable point either", and the review judge stays as it is. Nothing here changes
what ships: `chimera/fusion` never used this judge on this task (RESULTS.md says so); this is a
measurement of a decision we do not make yet.

## What this cannot show

The fusion judge's real job (comparing several answers to one question); comments without a cached
diff (dropped, as before); anything about the 60-line window's adequacy (the same window arms A–E used,
kept so the numbers pair); calibration on a production prior (the slice is 26% incorrect by
construction).
