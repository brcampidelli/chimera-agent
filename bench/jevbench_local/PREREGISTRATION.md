# Our local decision backend on the public JevBench — pre-registration

Written 2026-09-23, **before any item was sent to a model**. Local only (Ollama `qwen3:4b`), US$ 0.

## Why

Every number we have for the typed-decision layer comes from corpora we built (governance, blind_audit, the
factorial). Study 23 (the werr paper) showed what a self-built ruler invites. JevBench
(`github.com/fstandhartinger/jevbench`, MIT) is an **independent** public benchmark for exactly this shape —
state + typed question → a distribution over labels — with published results for 76 systems. Running our
backend on its public items gives the first number about `chimera decide` that someone else's ruler produced.

## What is measured, and what is not

* **Accuracy and calibration on the 231 public items only.** Not the composite JevBench Score: it
  puts 25% each on Speed and Cost, which a local free model wins by construction (the lesson of study 23), and its
  Intelligence axis blends in 308 sealed items we cannot see. So **this is not a leaderboard entry and will not be
  reported as one.**
* Items: `datasets/public/{original,easy,hard}.jsonl` at commit `2fa63fa3226cb369795525ed011800f57dcbd894`
  (sha256 prefixes `b2abe9ac8cabd953`, `314cd493a4c080fc`, `8c8f1efb5a04b701`); 72 + 48 + 111 = 231; 74 noul, 139
  choice, 18 score; every item's provenance says MIT. Fetched at that commit by the runner, not vendored.
* Scoring: **the benchmark's own functions** (`jevbench.scoring.score_task`, `jevbench.metrics.ece_top_label`,
  `brier_score`) on the distribution we return, so a scoring choice of ours cannot move the number.

## Instrument

The product path: each item becomes our `Choice` — options = the item's `labels` in their order, criteria =
the item's `criteria` (noul `true`/`false` mapped to `yes`/`no`), instructions verbatim — read by
`LocalLogprobBackend` (`qwen3:4b`, decision-first, schema-constrained, `temperature 0`, label token read from the
end, shares over the options). Probabilities are the shares, renormalised over the labels. No calibration map (none
exists for these questions): **raw** probabilities, labelled as such.

Clarified with the runner, still before any call: a noul goes through the product's `Noul`, which asks
`yes`/`no` in that order (JevBench lists `no`/`yes` — the same two words, so the shares map one to one); a
score's levels are the item's labels (`"0"`…) with its criteria list attached level by level; the JSON key is
`answer` for every item.

Our question linter is **bypassed** for the measurement (these are someone else's questions) and run separately:
how many of the 231 it would refuse is reported, because it is a fact about the linter.

## Arms

* **A_ctx** — `num_ctx 16384` on every request, `prompt_eval_count` checked below it on every response (the
  `spot_noul` lesson: Ollama truncates silently at its default 4 096).
* **A_ship** — exactly what `chimera decide` sends today, **no `num_ctx`**. Hard items run to ~3 700 tokens of
  state plus the rubric; this arm measures what the shipped command does to them.

## Outcomes

* **Primary:** accuracy (argmax = gold) on all 231, A_ctx, with a Wilson 95% interval; per file (original / easy /
  hard).
* Calibration: top-label ECE (the benchmark's 10-bin function) and Brier, A_ctx. **This is not JevBench's
  Calibration axis**, which is ECE plus fidelity to exact gold distributions on the hard tier; we report the two
  functions, not the axis.
* A_ship − A_ctx accuracy on `hard` (paired by item), and how many A_ship prompts hit the context ceiling.
* Items the backend could not read (no choice / no mass on the labels): counted as wrong, as JevBench counts them.
* Linter refusals among the 231.

**References, published by the benchmark (v1.4, public accuracy over the same 231):** Jev 1.13.0 **0.866**;
SemIf/OpenJev (Qwen3.5-4B, a fine-tuned 4B) **0.810**; the table's range 0.57–0.996.

## Predictions (written before running)

* A_ctx accuracy **0.70–0.80**: below Jev (0.866) and below the fine-tuned 4B (0.81) — ours is an untuned 4B read
  decision-first — with the loss concentrated on `hard` (long policies, traps).
* Calibration **poor raw**: ECE above 0.15 — the governance bench found `qwen3:4b` saturated before its map.
* A_ship loses **≥ 10 points on `hard`** against A_ctx, and more than half of the hard prompts hit the ceiling.

## Decision rule

No product change is decided by accuracy alone. Two things are decided here:

* **If A_ship loses ≥ 5 points on `hard` against A_ctx**, `LocalLogprobBackend` gains a context setting sized
  to the request (the default path of `chimera decide` must not truncate), in its own PR.
* The accuracy and ECE go into the `chimera decide` documentation as **the independent number**, with the
  references beside it and the caveat that none of JevBench's four axes was measured as the benchmark defines it.

## What this cannot show

The sealed items (and so any official score); speed/cost against the benchmark's other systems (different
hardware); any calibrated number (no map for these questions); other local models.
