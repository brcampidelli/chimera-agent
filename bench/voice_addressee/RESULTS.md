# S30-49 — "was this said to me?": results

Run on 2026-10-06 against local `qwen3:4b` at US$ 0. The corpus has 90 hand-written
transcripts: 60 `not_for_me` (side-talk, TV, self-talk, quotes, reading aloud) and 30 `for_me`
controls. Per the dated amendment, the spoken answer is the text after `</think>`. Raw rows are in
`results/voice_addressee.jsonl`; there were 0 spoken failures and 0 Choice failures.

| | count | rate |
|---|---:|---:|
| A — spoken path: non-directed speech that became a tool call or a long answer | **9/60** | 15.0% |
| … of which tool calls | 3/60 | |
| … of which long answers (≥ 40 words) | 7/60 | |
| A — directed controls answered | 30/30 | 100% |
| B — shadow Choice read `for_me` on non-directed speech | **52/60** | 86.7% |
| B — shadow Choice read `for_me` on directed controls | 30/30 | 100% |

The tool-call and long-answer counts overlap on one row, so they sum to 10 for 9 rows.

## Verdict, by the registered rule: **no useful separation**

- **A is below the bar the study set for itself.** The registration required A to answer at least
  50% of non-directed speech before a gate is worth building; it answered 15%. Three tool calls
  out of 60 side-talk utterances is real, but it is not the failure the paper (2609.32536) led us
  to expect, for the ASR-to-text path Chimera uses.
- **B fails.** The Choice reads 52 of 60 non-directed transcripts as addressed to the assistant,
  against a limit of 6. Read without context, "Can you grab the charger when you come upstairs?"
  or "Please send the invoice to the address I texted you" is a request a person could make of an
  assistant. The local decider takes the words, not the room.

Behaviour stays unchanged. The shadow Choice stays OFF, as registered.

## What this cannot show

- **No context.** These are text transcripts, with no speaker identity, no audio cues and no
  conversation history. A gate with those signals would be a different instrument.
- **One local model.** Other models may weigh these phrasings differently.
- **Side effects.** The three tool calls on side-talk were recorded but never executed, so what they
  would have done is not known.
