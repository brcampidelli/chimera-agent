# Pre-registration — S30-56: memory staleness, supersession, and persistent opinions

**Registered 2026-10-06, before the deterministic baseline and before any model call.** Model execution is explicitly deferred; this registration authorizes **US$ 0** of model spend. Target model: `qwen3:4b`, two replicas per item, only when separately authorized.

## Why

Memory can remain stale when a user's later statement changes an earlier fact without repeating the same key words. A stored opinion can also be repeated as if it were verified fact. This study measures current deterministic behavior first, then prepares (but does not execute) model-mediated update and opinion cases. `metadata["supersedes"]` currently records old text for an in-place writer update; this study tests explicit and implicit changes in the extraction/consolidation path and does not assume that archival metadata provides recall-time supersession.

The motivating references supplied with this study are STALE (arXiv:2605.06527) and PASB (arXiv:2607.10526; reported 71.9% vs 45.0%). Those are motivation, not results of this benchmark.

## Setup

### Deterministic supersession slice

A frozen, hand-authored set will contain direct changes of the form `X changed from A to B` and implicit changes such as `I moved to Lisbon` after `I live in Porto`. Each case begins with the earlier fact in memory, applies the later utterance via today's deterministic product path, and checks whether recall has the new value and whether the old value remains answerable. Run with no model and no network access. Publish every case and aggregate counts, including failures; do not discard inconvenient examples.

This is a diagnostic slice, not a population estimate. Its purpose is to establish whether the bug can be reproduced by a no-model path before implementing any fix.

### Deferred model set

Prepare approximately 45 Type I/II update items and approximately 30 opinion items, each opinion item paired with a downstream probe that distinguishes quoting an attributed opinion from adopting it as established truth. Freeze item text and grading rules before any model execution. Use `qwen3:4b`, two replicas per item. The runner must support an injected fake backend and test its scheduling, parsing, grading, and no-network/no-model path without invoking a real backend. **Do not run this model suite as part of this task.**

Type I: explicit correction/replacement of a previously stored fact. Type II: a changed fact conveyed implicitly or with paraphrase, including changed location, role, preference, or other stable personal fact. Opinion items: attributed personal beliefs or judgments; a downstream answer is correct only if it preserves attribution and does not present the opinion as independently verified truth.

## Metrics and decision rule

For deterministic updates report: number of cases; number of later facts recalled; number of old facts still recalled as current; and exact case-level outcomes. A wrong update is any case in which the new value is stored/recalled as current incorrectly, or the old value is represented as current after the update. Also report untouched/no-op and ambiguity cases separately.

For the deferred model set report Type I and Type II recall separately, wrong updates separately, and opinion-probe attribution accuracy. Report per-item replica disagreement; do not count two replicas as independent users.

**Ship rule:** keep the feature OFF unless Type I update recall rises versus the deterministic baseline with **zero wrong updates on the existing 48-item `bench/memory_extraction` set**. This is a conjunctive gate; higher recall cannot compensate for a wrong update. Preserve the existing 48 items and their labels unchanged. Run the 48-item deterministic check only if it is confirmed not to invoke a model. No model run is authorized here.

## Predictions

- **P1.** Current deterministic handling will miss at least one implicit update in the frozen supersession slice.
- **P2.** An opt-in supersession/semantic-near-fact implementation will increase update recall on the slice, but it will remain OFF until the 48-item zero-wrong-update gate is met.
- **P3.** The prepared opinion items will expose at least one downstream attribution error in the two-replica Qwen run; this remains untested until separately authorized.

Predictions are not findings. The baseline and all subsequent readings must be recorded in `RESULTS.md` without changing this registration.

## Execution boundary and owed commands

For this task, run only deterministic local checks, fake-backend tests, and static checks; do not launch Qwen or any other model. The future model command is to be written into the harness but remains owed pending separate approval. The report must state the exact command to run it, and explicitly state that it was not run.

The ship-gate command is the deterministic `bench/memory_extraction` check if its runner's `--check` mode is verified to avoid model calls. If it requires a model, do not run it; record the exact deferred command instead.

## Scope and provenance

This registration covers the baseline, benchmark preparation, and an opt-in/off-by-default supersession link plus semantic near-fact lookup. It does not authorize a default-on behavior change, paid API use, or a model evaluation. Keep raw item definitions and deterministic outcomes in the repository so the readout can be independently reproduced.
