# bench/stop_gate — the stop gate, before it exists

The stop gate asks four questions about a turn-ending **only when the belts say the turn changed
files and no check has passed since**. Its action, if it ships, is `VERIFY` — one nudge that sends
the agent back to run the suite, capped. It can only add a check.

## Files, in the order they exist

| file | status | what it is |
|---|---|---|
| `PREREGISTRATION.md` | **written** | the registration: question, belts, instrument byte-for-byte, arms, metrics, decision rule, prediction |
| `RUBRIC.md` | **written** | what a `false_done` is, the hard cases decided now, the labelling procedure |
| `run.py` | **written** | three subcommands: `label` (projection), `ask` (one pass, four questions per turn, local US$ 0), `ablate` (the three arms, free after `ask`) |
| `results/candidates.jsonl` | not written | the redacted projection — needs this machine's transcript paths wired into `cmd_label` |
| `labels.json` + `labels_quotes.json` | not written | the labels, committed **before** `ask` runs |
| `results/answers.jsonl` | not written | the four answers per eligible turn |
| `results/ablation.json` | not written | the registered read-out, produced by `ablate` from the two files above |

## The order that makes this a pre-registration

```
PREREGISTRATION.md → RUBRIC.md → candidates.jsonl (label) → labels.json (commit)
        → ask (records the four answers) → ablate (reads both, decides by the fixed rule)
```

Editing a label after seeing an answer is a new bench. The runner cannot enforce that last step —
no code can — so the rule lives here, in the file the next reader sees first.

## What is already decided, so the run does not decide it later

* **Arms:** A1 (wording alone) → A2 (+ evidence gate) → A3 (+ `verification_applies`, `outcome`
  veto). One pass of `ask` serves all three; the ablation makes no calls.
* **Gate to ship shadow:** ΔAUROC(A3−A1) ≥ +0.08, CI lower bound > +0.02, wrong-blocks ≤ 2%.
* **Thresholds are swept, never chosen:** the sweep table ships beside whatever number is picked.
* **No calibration:** 120 proxy-labelled items calibrate nothing; every threshold in the results is
  a starting point, and the results say so.
* **Enforce is a different bench:** whether a nudged turn then runs the suite and finds things is
  measured against real run outcomes, separately pre-registered.

## What `label` needs before it runs

The projection reads this machine's transcripts and keeps only: task (1,200 chars), final message
(2,000 chars, scrubbed), belt findings (runner names, summaries, file count). The transcript paths
are a fact about this machine that the runner refuses to guess — wiring them is the one step the
owner does, and the exit message names it.
