# The open interface, mapped over 1 000 items — phase-4 demo

2026-09-23 · `map_reduce.py` · local `qwen3:4b@Q4_K_M` through `chimera decide --jsonl`, `--no-log` ·
US$ 0 · raw in `results/` (`items.jsonl`, `questions.json`, `answers.jsonl`, `summary.json`).

The phase's gate was "a map-reduce demo on 1 000 items, local, timed". The items are this repository's
last 1 000 conventional commit subjects on `main`, the `type(scope):` prefix cut off and kept as the
answer key; one Choice asks what kind of change each subject describes.

## The interface — what the demo was for

| | |
|---|---:|
| items asked | 1 000 |
| answered | 1 000 |
| question errors / halts | 0 |
| wall time | 465 s — **0.46 s an item** |
| cost | US$ 0 |

The command, the route and the agent-facing function are one function
(`chimera/decisions/interface.py`); the round trip against this package's own Decisions client is in
`tests/test_decide_speaks_the_decisions_shape.py`.

## The model on this question — a description, not a benchmark

Accuracy **0.412** against a majority-class baseline of 0.343. The confusion says why:

| truth → answered | n |
|---|---:|
| other → other | 282 |
| **feature → other** | **258** |
| **fix → other** | **177** |
| docs → other | 80 |
| feature → feature | 76 |
| fix → fix | 51 |

The model puts most subjects in `other`, the option whose criterion is *anything else* — a catch-all
absorbs a small decision-first model's uncertainty. That is a fact about writing questions for this
interface, and it goes into the `system-one-design` skill: **a catch-all option is where an unsure
reading goes; give it a narrow criterion, or leave it out and read the probabilities.** It is not
evidence about any decision the product makes: no product surface asks this question.

## What this cannot show

Nothing about a calibrated decision (no map for an ad-hoc question); nothing about hosted backends'
throughput; nothing about questions with long states (a commit subject is ~60 characters — for long
states the local backend needs `num_ctx`, see `bench/spot_noul/RESULTS.md`).
