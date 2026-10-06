# Pre-registration — show disagreement-path candidates to the fusion synthesizer

**Registered before implementation, replay, or any model call.** Model calls are not authorized as part of this change.

## Question and motivation

On disagreement, `FusionEngine._run_synth` currently gives the synthesizer the task and judge analysis, but not the candidate answers that analysis describes. This is a lossy hand-off: the judge can omit or misstate a valid proposal, and the synthesizer cannot recover it from the input it receives. The paired intervention changes only whether the successfully returned candidate texts are appended to that prompt. The default remains OFF.

## Corpus and headroom

Use all rows in `bench/judge_blind_hard/results/collect-all.jsonl` (the 50 collected AIME validation problems; reference and three writer answers are already recorded). Do not fetch more data or make model calls during corpus selection.

The paired evaluation cohort is selected mechanically from those rows: `answers` has three strings, `correct` has three booleans, and at least one `correct` is true and at least one is false. Empty answers are allowed as recorded candidate outputs, and will be passed verbatim to the synthesizer; these rows retain an answer text and a correctness result in the seed, including the empty candidate's failure to answer. This identifies items where the best candidate is correct and a wrong candidate exists that the final answer can regress to. It does **not** condition on which answer the candidate-aware synthesizer produces. At preregistration, applying the primary selection rule to the seed yielded **16 qualifying rows** (the successful `correct` metadata does not require all answer texts to be non-empty); the harness must recompute and report the IDs and abort if the cohort is empty. The harness permits an empty candidate text when it appears in the seed (and passes it through verbatim), since emptiness is itself a recorded failed proposal, not a reason to discard an otherwise headroom-qualified item. Exclusions and the full source corpus size must be reported.

## Paired arms and procedure

For each qualifying item, make one call per arm, in a deterministic alternating order by item index, to the same local `qwen3:4b` model, at temperature 0, with the same answer budget and task wording:

- **A — as sent:** current production prompt, task/context plus judge analysis only.
- **B — candidates visible:** identical prompt with all three original candidate texts included verbatim, labelled in their original writer order. The judge analysis and candidates are exactly the stored seed material; do not generate a new judge analysis or panel.

Use the same fixed judge analysis in both arms: the seed collection's `judge_alone` outputs are not included, so the harness must supply a fixed, arm-invariant statement that points the synthesizer to the candidate set without stating which is correct. Record that statement in each result. Alternatively, if a row has a stored judge analysis in a future corpus revision, it must be used identically in both arms and cannot be revised after seeing outcomes. The local model must be served by Ollama; the command below is expected to cost **US$ 0**. Do not run it until the owner authorizes measurement.

## Outcomes and scoring

Extract the final integer using the seed benchmark's `extract_answer` / `normalise` convention and compare with the stored `reference`.

**Primary:** best-candidate regression rate, per arm: among cohort items where the best candidate is correct (all selected cohort items), fraction whose final answer is wrong. Report paired item counts as well as rates. The intervention effect is A regression rate minus B regression rate; positive favors candidate visibility.

**Secondary:** final-answer accuracy in each arm and the paired absolute percentage-point difference (B minus A). Report missing/unparseable answers as incorrect and separately count them.

Use exact two-sided McNemar on the paired binary correctness outcomes (report discordant pair counts, exact p-value, and paired effect). Also report the unadjusted absolute percentage-point differences. No per-problem exclusions after calls begin. No repeated calls, best-of sampling, or selective reruns; a transport failure invalidates and restarts that item's pair before unblinding either answer. Publish all per-item outputs and costs (expected zero) before interpretation.

## Decision rule and prediction

The practically meaningful threshold is an **absolute 10 percentage-point reduction** in the primary regression rate (A minus B). Claim support only if candidate visibility reduces regression by at least 10 pp and the paired accuracy difference is non-negative; regardless of significance, publish the observed result and McNemar test. A reduction below 10 pp, no reduction, or harm is a null/negative result, not grounds to widen the cohort or alter the prompt post hoc.

Prediction: visibility reduces regression because the candidate text restores evidence omitted by the judge. The direction is uncertain; no statistical-power claim is made for this small, selected cohort.

## Reproducibility and command owed

The offline fake-backend replay test is run during implementation; it makes no model requests. The harness at `bench/fusion_synth_candidates/run.py` reloads the 50-row seed, selects rows with exactly three candidate strings, at least one correct and at least one incorrect (and aborts on an empty cohort), calls the paired prompts in alternating order, records both raw outputs and parses using the seed benchmark's `extract_answer` / `normalise` convention, and writes paired rows plus the summary. The runner computes the exact two-sided McNemar p-value from discordant pairs and refuses non-local endpoints and existing output files. The live measurement is **not** run for this task. Exact command owed:

```cmd
uv run python bench/fusion_synth_candidates/run.py --model qwen3:4b --endpoint http://localhost:11434/v1 --output bench/fusion_synth_candidates/results/qwen3-4b.jsonl
```

The command must refuse to overwrite an existing output and use only the caller's local Ollama-compatible endpoint. No credentials or paid service are used.

## Limitations

This is a selected AIME slice derived from three particular writers, not an estimate of general fusion accuracy. The seed's original judge-alone screening is not part of this intervention. AIME contamination, extraction error, model sampling variation, and the small cohort limit generalization. Seeing candidates may anchor the synthesizer as well as restore omitted evidence; this one paired run cannot separate those mechanisms.

## Amendments

None.
