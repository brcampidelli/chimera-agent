# Results — H7: the sharded-recap arm stopped at its pilot gate (uninformative)

2026-09-25. Pre-registration: `PREREGISTRATION.md` (commit `1ee92a1e`), Amendment 1 (`912f0879`). Data: `results/pilot.json`. Spend: **US$ 0.057 measured** (US$ 0.046 for this pilot and US$ 0.011 for the discarded first launch). The ledger counts US$ 0.066, because the discarded launch was rounded up to US$ 0.02.

## The decision

The pre-registered gate reads the FULL − SHARDED-A pass gap on the pilot. Under 10 pp, the recap has nothing to recover: the arm stops and is reported as **uninformative**.

| condition | pass (final reply, all hidden tests) | Wilson 95% |
|---|---:|---|
| F — whole spec in one turn | **30/32 = 93.8%** | [79.9, 98.3] |
| A — one shard per turn, shipped prompt | **29/32 = 90.6%** | [75.8, 96.8] |

- **Gap F − A = +3.1 pp**, Newcombe paired 95% [−12.5, +19.0].
- Discordant pairs: 3 tasks where only F passed, 2 where only A passed. Exact McNemar p = 1.0; task-level sign-flip p = 1.0.
- **3.1 < 10, so the arm stopped.** SHARDED-B (the recap) and the placebo were never run, and the main run did not happen.
- **The recap sentence is not a candidate for the `interactive` L1 module on this evidence.** It was not measured, because the loss it is meant to fix did not appear on the product's default model under this protocol.

**No reading of the failures could move the gate.** All five failures were read by eye, as registered, and each is a genuine miss of a stated requirement:

| run | what the final code does | requirement missed |
|---|---|---|
| F, `t01` | its regex accepts lower-case units only | case-insensitivity |
| F, `t10` | lines keep trailing spaces | "no line may end with a space" |
| A, `t13` | the counterclockwise order is wrong | the counterclockwise order |
| A, `t24` | 90.7 s prints "1.0 minute ago" | "whole unit, rounding down" |
| A, `t25` | "2 3" is accepted as an expression | malformed expressions raise |

Even if both FULL failures were counted as defects, which they are not, the gap would be 32/32 − 29/32 = 9.4 pp, still under the gate.

## What the pilot does show

**The paper's mechanism is fully present, but its loss is not.**
- SHARDED-A answered the vague first shard with complete code in 32/32 conversations. 124 of its 128 intermediate turns carried code. These are the premature answer attempts arXiv 2505.06120 blames for the loss.
- Yet by the fifth turn the model had folded every shard in, as well as it did when given everything at once.
- Of A's three failed finals, two missed an earlier-shard test and one missed the last shard. n = 3, so this is not a reading of "forgetting".

**Two-sided outcomes.**

| | F | A |
|---|---:|---|
| halts | 0 of 32 | 0 of 32 (192 calls, all `finish=stop`, one attempt each) |
| no-code finals | 0 | 0 |
| US$ per conversation | 0.00051 | 0.00093 (1.8×) |
| final-turn completion tokens | 2,700 | 861 |
| seconds per conversation | 36 | 49 |
| finals opening with a list of ≥ 3 items | 0 | 0 |

- A reaches its final answer with a third of FULL's final-turn reasoning, having built the function up turn by turn.
- Cache: 79,104 of 159,903 input tokens were served from the provider's cache, all on the growing sharded histories. Cost is priced at the full input rate, so the dollar figures slightly overstate spend.
- Nothing lists requirements unprompted: the recap behaviour, if it ever appears, would come from the sentence alone.

## Something the discarded launch surfaced

This comes from a discarded launch, not from the result: n = 9, it was not paired and it was not kept. It still bears on plan defect 5, "the fusion panel is told to use tools it does not have".

The first launch used the bare `DEFAULT_SYSTEM_PROMPT` on calls that carried no tools. On this model:
- The replies narrated file work that never happened: *"I've created a `slugify.py` file"*, *"no changes needed"*, *"Let's read the current file and edit it"*.
- **3 of 9** sharded conversations then ended on three empty replies in a row.
- After Amendment 1 added one surface line saying that no tools are available, 192 of 192 calls finished normally with a reply.

The cause of the empty replies was not recorded at the time (the runner now records it), so this is a lead, not a measurement. The prompt's "use the provided tools… create the files" is a live hazard on any tool-less surface that inherits it, and the fusion panel is the one the inventory names.

## What this cannot show

- **That multi-turn sessions lose nothing.** The gap's interval, [−12.5, +19.0], does not exclude a real loss of up to about 19 pp. The gate is the coordinator's rule on the point estimate, with n = 32 tasks × 1. The correct label is "uninformative", not "no loss".
- **The regime where the loss is expected to be real.** Four features of this design all soften the setup relative to the paper:
  1. **The closing line.** Every conversation ends with "please give me the complete, final version of the code". That line is itself a mild recap cue, and the paper's user does not add it.
  2. **Deterministic shards.** The paper's LLM-simulated user rephrases the shards.
  3. **Conversation length.** Five turns only.
  4. **One model.** The strong product default only; weak or local models, such as S3 on ollama, are not covered.
- **How chat actually sends history.** `ChatSession` today does NOT send a multi-turn message list. It flattens the last six turns into one user message. This arm measured a real message list, which is what the Code screen's `CodeSession` sends, not what chat, the TUI or Discord send. Beyond six turns the flattened form drops whole turns, and no system-prompt recap can list what is no longer in the prompt.
- **Tools, real users, other days, providers, temperatures and task kinds.** Unchanged from the pre-registration.
- **The bare shipped prompt on a tool-less surface.** Amendment 1 shows that combination does not produce a usable conversation on this model, so what was measured is the shipped prompt plus a surface line.

## If H7 is reopened

Keep the recap arm unchanged. What needs changing is the instrument, which must first show a gap ≥ 10 pp on its positive control. Candidate regimes:
- no closing line, with the final answer read after the last shard as the model left it;
- 10 or more shards;
- the flattened `ChatSession` form;
- a weaker or local model.

The corpus, the grader (328 tests, reference 32/32, shard-0-only solution failing 112/128 later requirements) and the runner are reusable as they stand.
