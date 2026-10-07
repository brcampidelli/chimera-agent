# S30-53 — the synthesizer sees the candidates: results

Run on 2026-10-06 with local `qwen3:4b` through Ollama's native `/api/chat` (amendment A2), at
US$ 0. The cohort is the 16 rows of `bench/judge_blind_hard/results/collect-all.jsonl` that qualify
under the registered rule: three candidates, at least one right and at least one wrong. 34 of the
50 source rows were excluded by that rule. Raw outputs are in `results/qwen3-4b.jsonl`; none was
missing or unparseable.

| | A — as sent (task + judge analysis) | B — candidates visible |
|---|---:|---:|
| best-candidate regression (primary) | 7/16 = 43.8% | **0/16 = 0.0%** |
| final accuracy | 9/16 = 56.3% | **16/16 = 100%** |

Paired: 7 items flip from wrong under A to right under B, and 0 flip the other way. The exact
McNemar p is **0.016**.

## Verdict, by the registered rule

The rule asks for a reduction of at least 10 pp in regression with a non-negative paired accuracy
difference. **Observed: −43.8 pp regression and +43.8 pp accuracy. Support is claimed.** Under the
owner's standing rule (a setting the bench recommends becomes the default), `candidates_visible`
flips to ON in a separate change.

## What this cannot show

- **Small and selected.** The cohort is 16 AIME problems chosen because a right candidate exists to
  be found. The registration says so and makes no power claim. On items where every candidate is
  wrong, the arm has nothing to recover and could be distracted.
- **The synthesizer only.** The judge's own errors are not measured here.
- **Copying versus reasoning.** Whether B wins by reasoning or by recognising the majority is not
  separated. All 7 recoveries copy an answer that a candidate already had.
- **Scope.** One local 4B model on maths, with reasoning arriving inline (amendment A2). Hosted
  synthesizers on prose tasks are untested.
