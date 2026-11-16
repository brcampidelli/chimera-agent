# Proportionality block — results

Pre-registration: [`PREREGISTRATION.md`](PREREGISTRATION.md) (committed `2e7e55b0`, before any paid call;
§9 added during collection, see below). Run 2026-11-16, `openrouter/deepseek-chat-v3.1`, 150 runs
(75 pairs: 36 read, 33 edit, 6 control), 0 harness errors, **US$ 1.12** (ceiling 5.00).
Raw rows: `results/run1/runs.jsonl`; reading: `results/report.txt` (`python report.py results/run1/runs.jsonl`).
Switches in effect (product defaults, not pinned to legacy): `results/run1/switches.json`.

## Verdict: NOT ADOPTED (by the pre-registered rule)

| | OFF | ON | delta ON-OFF | interval (t over items) | meets |
|---|---:|---:|---:|---|---|
| P1 read: steps | 2.08 | 2.11 | +0.03 (+1.3%) | [-0.16, +0.22] | no |
| P2 read: output tokens | 213.9 | 193.5 | -20.4 (-9.5%) | [-59.8, +19.0] | no (spans 0, and short of the -10% floor) |
| G1 read: answer correct | 36/36 | 36/36 | 0 pp | | holds |
| G2 edit: tests pass | 29/33 (87.9%) | 30/33 (90.9%) | +3.0 pp | [-3.7, +9.8] pp; TOST at -5 pp: non-inferior | holds |
| G3 control | bakery 0/3, specific 3/3 | bakery 2/3, specific 3/3 | | | holds |

Secondary: edit steps 14.0 vs 14.2, edit output tokens 795 vs 833, prompt tokens per read run 8,154 vs
8,538 (the block's own length), US$ 0.081 vs 0.083 (read), 0.425 vs 0.447 (edit). Edit failures are
`constant_move_module` 3/3 in both arms and `param_thread_through` once in OFF: the same tasks, no ON-specific
failure. No ON edit run left the workspace unchanged (0 of 33).

## What this says

- The audit's hypothesis (the base prompt makes read questions cost steps) was **not borne out** on this
  model and corpus: a fixture question already takes about two steps (one read, one answer), so the room is
  tiny, and the block moved steps by +0.03. Output tokens fell 9.5%, an interval that spans zero.
- The block did not hurt: edits 87.9% -> 90.9% (one ON-only pass, no OFF-only), answers 100% both. That is a
  non-inferiority reading with a wide interval (about +-7 pp), not evidence of equality.
- Side observation, n = 3 per arm, not decisive: the bakery request ("faz um site pra minha padaria", an empty
  workspace) met the criterion "asked a question and wrote no file" in **0/3 OFF** runs and **2/3 ON**. The base
  prompt's bakery exception did not hold for this model in this harness. This is a hint to measure, not a
  finding; it is a reason the block is not worthless, and it is not what was registered.
- Measured on **deepseek-chat-v3.1 only** (§14): "no effect here" says nothing about another family.

## Limits (as registered in §7, plus what the run showed)

Corpus of three modules and eleven toy packages, written in one voice, not mined from real conversations; regex
checker; no same-length placebo arm (§13), so an effect that came from length could not have been separated
from one from content (there was no effect to separate). The 5 pp margin is under two runs at n = 33.

## Amendment disclosed (PREREGISTRATION.md §9)

§5 said "cluster bootstrap". `bench/PROTOCOL.md` §11 forbids a percentile bootstrap under N = 100, so before
analysing the full data the interval became the closed-form t over per-item means (`mean_t_interval`). The
decision and all thresholds are unchanged. Run on the first version of `report.py` for the five first pairs only.

## Decision

`PROPORTIONALITY_BLOCK` stays in `chimera/core/agent.py`, **off** (`CHIMERA_PROPORTIONALITY`), with this number in
its docstring and in the registry (`loop.proportionality`, status `null`, "measured on deepseek-chat-v3.1").
