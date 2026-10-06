# Study 30, S30-54 — decision-readout invariance

**Pre-registration; written before any model call.** US$0, local Ollama only. The model will not be run as part of this change. This file must be committed before implementation or measurement.

## Question and scope

Can the local decision backend's losses and instability be attributed to its answer rendering/readout rather than its model? Prior observations motivate, but do not establish, the hypotheses: JevBench reports 81.4% for prefix-free numeric ids with a prefilled `Best answer: [`; AnyJev reports that single-token labels with K-rotation averaging reduce order flips from 0.33 to 0.14; relabelling can change outcomes, and negation is frequently ignored. Current local JevBench accuracy is 0.619 on 231 public items, with 24 previously unread; the existing governance run had 5/55 verdict flips under reversed option order. These are historical baselines, not predictions or results of this study.

Two fixed populations, no tuning or item exclusion after inspecting responses:

1. **JevBench:** the same 231 public items and pinned clone/dataset hashes in `bench/jevbench_local/PREREGISTRATION.md`; score using JevBench's own functions. No leaderboard claim.
2. **Governance:** the fixed 55 `two_sided_items()` in `bench/jev_decisions/run.py`, with their authored attack/benign labels and original states. Preserve item ids and pairing. This is a smoke-sized, authored corpus, not a population estimate.

Every arm is a new prompt/readout instrument and gets a distinct SHA-256 instrument hash. Results are raw and uncalibrated. **The governance Platt map must be refitted against the selected instrument before any such arm can ship or govern a decision. No existing map may be reused.**

## Arms (all paired to the unchanged current instrument)

Run every applicable arm on both populations; record unsupported cases as not applicable before requests, never silently drop them from denominators. The baseline is the current instrument byte-for-byte. All arms use identical state, model/build, decoding settings, option meaning, and scoring code except for the registered change.

- **C_numeric:** prefix-free numeric ids for each option, rendered without semantic labels in the choice list; numeric ids are mapped back to the original options. Prefill assistant continuation `Best answer: [`; accept only the corresponding numeric id as a valid readout. Option ids are assigned in source option order.
- **Letters + K rotation:** single-token A… labels, option definitions kept explicit, ask once for each of the K cyclic rotations (K = number of options, maximum 10; larger choices are `not applicable`); align probabilities to option meaning and average across rotations. One registered arm, with the individual rotation results retained.
- **Two-call label-swap:** call 1 uses the original label-to-definition assignment. Call 2 swaps the first two labels while keeping the definitions and all other text fixed; translate both answers back to meaning. Report each call and whether the mapped verdict changes. This tests label dependence, not a replacement voting rule.
- **Neutral vs definition-only:** paired rendering contrast. Neutral uses `Choice.neutral()` (neutral letters with option names embedded in criteria, as currently defined); definition-only uses neutral letters and criteria containing only the original definitions, omitting the original option names. Keep both renderings separate; report their comparison to baseline.
- **Negated vs affirmed:** for binary items only, paired, meaning-equivalent question wordings: affirmed asks whether the item satisfies the criterion; negated asks whether it does **not** satisfy the same criterion. Invert the negated answer back to the affirmative meaning before scoring. A question without a defensible binary transformation is marked N/A at corpus-audit time, before model calls; publish its id and reason. Do not mechanically prefix arbitrary prose with “not”.

## Outcomes and analysis

For JevBench, report accuracy / 231 (unread counts wrong), top-label ECE and Brier on valid probability readings **and** the valid-item count; report unread count for every arm against the historical 24. For governance report accuracy, ECE and unread on all 55 (unread is incorrect); verdict flips against baseline and paired McNemar exact two-sided p-value (baseline-only correct vs arm-only correct), including discordant counts. For rotation averaging, also report flips versus baseline and per-rotation flips. For two-call swap, report mapped label agreement and per-call accuracy. For negation, report paired accuracy and mapped-answer flips on the pre-audited eligible subset, with n. Publish every item-level row, raw outputs, option probabilities, instrument hash, model tag and resolved quantisation/build; no selective summaries.

ECE uses 10 equal-width bins over valid top-label confidence (publish bin counts and the exact computation). Missing/invalid probabilities are reported as unread, not assigned confidence. McNemar uses exact binomial on discordant binary correctness outcomes; no continuity approximation. No significance correction is needed for a single confirmatory primary: accuracy difference on 231 JevBench items. Other contrasts are descriptive/exploratory; show all arms, no winner selection by a secondary metric.

## Predictions (falsifiable)

- C_numeric and letters+rotation will improve JevBench accuracy over 0.619, reduce unread below 24, and reduce governance verdict flips below 5/55.
- Definition-only will reduce dependence on original semantic label words relative to `Choice.neutral()`; the two-call swap will reveal non-zero label dependence.
- Negation will have lower mapped-answer agreement than affirmed wording. An effect absent on the eligible subset is a null result, not evidence about unsupported questions.
- Calibration may worsen despite accuracy gains; no claim of improved calibration is predicted. ECE can be unstable on 55 items.

## Absolute decision rule

**No rendering mode becomes the default or is enabled for a production decision on this run alone.** An arm is eligible for a separately reviewed proposal only if it has no greater JevBench unread count than baseline, does not reduce JevBench accuracy, does not increase governance unread or governance error count, and has a strictly lower governance flip count; report paired exact McNemar and uncertainty regardless. If no arm meets every condition, retain the unchanged default. Even if one meets them, shipping is blocked until a governance calibration map is independently refitted and validated for that exact instrument hash/model build, and its safety owners approve it. Measurement does not itself authorize a map or a product switch.

## Measurement command (owed; do not run now)

After implementation and a local Ollama `qwen3:4b` build is available, run the harness over both fixed corpora:

```cmd
uv run python -m bench.decision_readout.run --jevbench C:\PATH\TO\PINNED\jevbench --base-url http://localhost:11434
```

This command is intentionally not run for this task. It is local-only and makes model requests. The harness must print the number of requests before the first request, retain resumable item rows, and refuse a JevBench clone whose commit or dataset digests differ from the pinned preregistration.

## Limits

The 55 governance items are authored and small; the 231 public JevBench items do not represent production governance. A result cannot establish general robustness, erase model/build effects, or justify deployment. Every changed rendering is a new instrument, and the current governance Platt map is invalid for it until refit.

## Sources / starting evidence

`bench/jevbench_local/RESULTS.md`; `bench/jev_decisions/RESULTS.md` and `PREREGISTRATION-tier-b.md`; `chimera/decisions/contract.py::Choice.neutral`; `chimera/decisions/lint.py` (`negated` remains a warning). Papers cited in the request: arXiv:2610.02076, 2610.00831, 2610.02586. This registration makes no claim that those findings transfer to this backend.
