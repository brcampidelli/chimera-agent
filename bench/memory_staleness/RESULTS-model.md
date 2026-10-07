# S30-56 — model run on qwen3:4b: results

Run on 2026-10-06 with `ollama_chat/qwen3:4b` at temperature 0, 2 replicas, and an 8,192-token
budget (amendment 2). It cost US$ 0. The 150 responses are in `results/model.json`. The two
replicas produced identical answers on every item, so replica disagreement is 0, and the counts
below are per item, not per replica.

## Readings

| | registered grader | audited by reading every flagged answer |
|---|---:|---:|
| Type I update recall | 20/23 | **23/23** |
| Type II update recall | 21/22 | **21/22** |
| old value given as current | 1 (u29) | **1 (u29)** |
| opinion probes that keep the attribution | 13/30 | **30/30** |

## The grader is the defect, and the readings say so

The registered grader fails answers that mean exactly what the reference means:

- **Opinions.** It requires the first-person sentence verbatim. All 17 failures answer in the third
  person: "The user believes classical music is the best study soundtrack", "The user considers Rust
  unnecessarily difficult". Each keeps the attribution and none states the opinion as fact.
- **Updates.** It requires the new fact's changed words. "3" for "three children", "mornings" for
  "morning", and "I started cycling to the office instead" for "bicycle" are all counted as missed
  updates. All three are correct.

An answer equivalent to the reference cannot fail; when it does, the defect is in the ruler. The
grader is not changed after the run. Both readings are published, and the audited one is the one
that describes the model.

## What the model actually got wrong

**u29 (Type II):** the user said "I accepted a role as a data analyst", and the model answered
"product designer", the superseded value. It is the only stale answer in 45 update items. It is an
implicit change, which is exactly the class the pre-registration predicted would be hard.

## Against the predictions

- **P3** ("the opinion items will expose at least one downstream attribution error"):
  - Confirmed on the registered grader (17 of 30 flagged).
  - **Refuted on the audit** (0 of 30).
  - The registered confirmation is an artefact of the grader.
- **The ship rule.** It cannot be read from this run, because it compares the OFF `supersedes` link
  against the 48-item extraction set. That check is still owed and needs an embedder and a
  definition of a wrong link (see the PR). The feature stays OFF.

## What this run cannot show

- Recall when the fact must survive extraction and storage. Here both facts were handed to the model
  in the prompt, so this measures reading two facts, not memory.
- Any model other than local qwen3:4b, or thinking mode as Chimera configures it elsewhere.
