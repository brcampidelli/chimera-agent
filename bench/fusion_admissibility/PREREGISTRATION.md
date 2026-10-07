# S30-52 — fusion admissibility and answer-level agreement

**Registered before inspecting the per-member result rows.** US$0; this is a replay of files already on disk. No model calls.

## Question

Does the current fusion early-stop rule compare wording rather than answers, and is the available panel sufficiently independent to justify treating agreement as corroboration? Separately, can exact duplicate outputs be removed without changing the fusion result?

## Inputs and exclusions

Replay the per-member rows in `bench/judge_blind_hard/results/collect-all.jsonl` and, when present, `bench/fusion_aggregate/results/panel.jsonl`. Use `bench/panel_correlation/measure.py` and its already-published ICC as the cross-check for the panel-correlation estimate. Do not regenerate, modify, or fill missing rows. Report unavailable/malformed inputs rather than reconstructing them. No network or model calls.

## Registered measurements

1. **Member agreement:** Cohen's kappa for each member pair on item-level correctness, with observed error correlation (Pearson correlation of binary error indicators) for each pair and the mean pairwise error correlation. Report sample sizes and handle constant error vectors as undefined, not as zero.
2. **Agreement rules:** evaluate both the existing phrasing-ratio rule (the shipped `agreement_threshold`, default 0.8) and answer-level agreement (normalised extracted final answers are exactly equal). Report firing rate (items triggering the rule) and precision (fraction of triggered pairs where **both members independently return the reference answer**). A missing/unextractable answer is not an agreement; report its count. Preserve the shipped phrasing computation as implemented, not a post-hoc tuned threshold.
3. **Call savings:** for each rule, report estimated member calls saved at `--best-of >= 3`, using observed triggering items and the actual 3-member panel: one member call saved by selective escalation, and two total provider calls saved if the full panel would otherwise run. Label this replay estimate, not a live runtime measurement.
4. **Admissibility:** report the per-member correctness kappas and error correlations, and the previously published `panel_correlation` ICC(1). The hard AIME panel is the decision corpus; the arithmetic corpus is reported separately and not allowed to override a ceiling-limited result.
5. **Exact locking:** compare the current fusion output to a fake-backend replay with exact duplicate outputs collapsed. Locking is admissible only if output is byte-for-byte unchanged, selecting the longest member from an identical prefix. This is a behavioural test, not a model-quality claim.

## Decision rule (locked)

Answer-level early-stop agreement is eligible to ship only if its firing rate is higher than the existing phrasing rule **and** precision is not lower. Even if eligible, answer-level agreement remains **OFF in this change**; a replay alone does not authorise enabling a new stop condition. Exact duplicate-output locking may ship **ON** only if the fake-backend test proves exact output preservation. Do not infer panel admissibility from answer agreement or use the reported ICC as a universal discount factor.

## Reporting

`RESULTS.md` will give the source paths, replay shape, all registered statistics, missing data, the rule comparison and decision, plus the admissibility verdict alongside the published ICC. No thresholds or exclusions will be changed after observing the rows.

## Reproduction

`python bench/fusion_admissibility/run.py` reads only the existing JSONL artifacts and writes/prints the deterministic readout. It makes no model calls.

**Decision:** preregistered before reading the row-level results; this file is committed before running the replay.
