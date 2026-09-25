# Pre-registration — H7: does a recap before the final answer help a sharded, multi-turn session?

**Registered 2026-09-25, before any call.** Study 25, wave 3, arm H7 (`bench/PLAN-study25-system-prompts.md` §7 S2/S3/S10, §9). Hard budget **US$ 1.50**, pilot included; expected about US$ 0.9–1.3.

## Why

arXiv 2505.06120 ("LLMs Get Lost in Multi-Turn Conversation") takes a fully specified instruction, splits it into shards and reveals one shard per user turn. Every model it tested did worse than when it got the whole instruction at once; the paper reports an average drop of 39% over 15 models and six tasks. Its harness-side RECAP and SNOWBALL strategies recover only part of the loss.

H7 asks whether a **model-side** version of the recap, one sentence in the system prompt, recovers some of that loss on Chimera's own prompt and default model. Nothing is re-injected by the harness.

## Setup

**Tasks.** `tasks.py`, frozen here: 32 small Python function tasks, each fully specified by exactly five shards.
- Shard 0 is the goal and names the function.
- Shards 1–4 each add one requirement: an edge case, a parameter, a return format or an error behaviour.
- The hidden tests (328 in all, 7–15 per task) are tagged with the shard whose requirement they check. The model never sees them.
- Every expected value follows from the shard text.

**Grader preflight, run before this file was committed ($0, `run.py --check`).**
- Each task's reference solution passes all of its tests: 32/32.
- Each task's shard-0-only solution fails at least one later-shard test: 32/32. In total it fails 112 of the 128 later-shard requirements, so the tests can see a requirement being dropped.

**Conditions.** One call per user turn, plain `LLMGateway.complete`, no tools.

| id | user turns | system prompt |
|---|---|---|
| **F** (FULL) | 1: the five shards joined by blank lines, then the closing line | `DEFAULT_SYSTEM_PROMPT` |
| **A** (SHARDED-A) | 5: one shard per turn, the closing line appended to the fifth | `DEFAULT_SYSTEM_PROMPT` |
| **B** (SHARDED-B) | as A | `DEFAULT_SYSTEM_PROMPT` + blank line + the recap sentence |
| **P** (SHARDED-placebo) | as A | `DEFAULT_SYSTEM_PROMPT` + blank line + the placebo sentence |

- F is the paper's CONCAT condition: the same bytes as the shards, in one turn. The F–A difference therefore measures turns, not rewording.
- F is the **positive control**: it must beat A, or the instrument cannot show the loss the recap is meant to fix.

**Frozen texts.**

- The recap sentence (the coordinator's draft, unchanged):
  > When the conversation has run over several turns, start your final answer by listing, in one short list, every requirement the user has given in any turn; then give the answer that meets all of them.
- The placebo (PROTOCOL §6: equally long, irrelevant, same slot; 224 against 199 characters):
  > Chimera is open-source software released under the Apache-2.0 licence; its source code, its issue tracker and its documentation are all kept together in one public repository that anyone may read, copy, change or build from.
- The closing line, appended to the last user turn in every condition, so that every conversation ends on a request for the whole code rather than a patch:
  > That is everything. Please give me the complete, final version of the code.

**The prompt under test, and why this one.**
- `run.py` builds the system message the way `chimera chat` does: a plain `Agent` composing its prompt. The per-install layers are off:
  - the owner identity (`agent.json`);
  - the workspace `AGENTS.md`;
  - keyword-retrieved skills and bundles;
  - the todo sentence, which appears only when `todo_write` is granted, and no tools are granted here.
- What remains is asserted to equal `DEFAULT_SYSTEM_PROMPT` byte for byte, and that is the prompt under test.
- The per-install layers are left out because they vary per install, and skill retrieval varies per message: FULL and SHARDED would retrieve against different text, a confound.

**The simulated user.**
- Deterministic: turn *i* is shard *i*, verbatim, whatever the model said before. The user never answers a question directly.
- If the model asks something, its next user turn is the next shard. Shards are exactly what a person adds as a conversation goes on.
- A question that no shard answers is not answered in F either, so F and SHARDED stay symmetric.
- This also removes the simulator's own model call, its cost and its variance.
- The model's intermediate replies stay in the history, as in the paper: the assistant's answer text, without reasoning.

**Model.**
- `openrouter/deepseek/deepseek-v4-flash-0731`, the product default.
- Pinned to DeepInfra with `allow_fallbacks: false`.
- Temperature 0.2, the chat surface's `AgentConfig` default.
- No `max_tokens`, so the gateway's 32,000-token ceiling applies, as in chat. Reasoning is left at the model's default (on), as in chat.

**Grading, deterministic.**
- Every fenced code block of the **final** reply is executed in order in one namespace, in a child process; a later definition replaces an earlier one, and a failing block is skipped.
- Then every hidden test runs on its own, with a 5-second limit.
- A conversation **passes** when the required function names exist and every test passes.
- A final reply with no block defining the names is a fail, counted separately as **no-code**.

**Units and order.**
- A unit is one (task, replica). Its conditions start together, so they share the same minutes and route.
- Five units run at a time (eight in the pilot).
- Replica 1 of every task runs before replica 2 of any, so a stop leaves whole replicas.

## Pilot, and how n is fixed

- **Pilot:** every task once, conditions F and A only (32 pairs), about 190 calls. The pilot's runs are **not reused** in the main run.
- **Gap rule:** gap = F pass rate − A pass rate over the pilot pairs. If the gap is **under 10 pp**, the recap has nothing to recover: the arm stops and is reported as **uninformative**.
- **Reading before the main run:** before the main run, the tests that F fails in the pilot are read. A test whose expected value does not follow from the shard text, or an extraction that misses code the reply plainly contains, is a defect. It is fixed in a dated amendment, committed before the main run.
- **n.** The independent unit is the task, so the honest n is bounded by 32. Replicas are correlated: at this project's ICC(1) ≈ 0.7 (PROTOCOL §8), three replicas carry about 1.24 observations and a fourth and fifth add about 0.04 between them. §8's table asks for about 160 pairs for a 10 pp effect at p_d = 0.20, and 32 × 5 = 160 meets it in pairs, not in independent observations. So the main run's size is fixed from the pilot's measured cost per conversation (`pilot_decision` in `run.py`). It takes the first of these options whose projected cost fits 90% of what remains under US$ 1.40:
  1. k = 5 with P;
  2. k = 4 with P;
  3. k = 3 with P;
  4. k = 5 without P;
  5. k = 4 without P;
  6. k = 3 without P.

  If none fits, the arm stops. The placebo outranks replicas 4–5 because they buy almost no power and §6 asks for it.

## Metrics

- **Primary: SHARDED-B vs SHARDED-A pass.** Paired per (task, replica), with the exact two-sided McNemar test and Newcombe's paired interval. It is reported beside a **task-level** sign-flip randomisation test on the per-task mean difference (exact when at most 20 tasks differ), because replicas of one task are not independent.
- **Positive control:** F vs A, the same way.
- **Floors:**
  - replay: disagreement between replicas of the same task within one condition;
  - perturbation: P vs A, the same text slot with neutral words;
  - endpoint: pinned;
  - cache: `cache_read_tokens` recorded per call (a hosted route cannot be forced cold, PROTOCOL §3).
- **Mechanism-active:** the share of final replies with at least three list items before the first code block, per condition. If B's share is no higher than A's, the sentence did not act, and a null means "not taken up" rather than "does not help".
- **Guards:**
  - no-code rate per condition;
  - cost per success (US$, DeepInfra list price, cache reads priced at the full input rate);
  - final-turn completion tokens.
- **Secondary, descriptive:**
  - among failed finals that contain code, the share failing a test from an **earlier** shard (a lost requirement) rather than only the last one;
  - intermediate turns that already contain code (premature answer attempts, the paper's mechanism);
  - seconds per conversation.
- **Halts** (PROTOCOL §2) — a provider error after three attempts, an empty reply after three attempts, a reply truncated at the ceiling, or a different provider answering — are neither pass nor fail. A halted conversation leaves its pair.

## Predictions (written so they can be wrong)

- **P1.** F passes at least 80%, and A is at least 10 pp below F (the paper's direction).
- **P2.** B − A lies between 0 and +8 pp, recovering less than half of the F − A gap, and is not significant on the task-level test.
- **P3.** Mechanism: at least 70% of B's final replies open with a list of three or more items, against at most 25% of A's.
- **P4.** At least half of A's failed finals with code fail an earlier-shard test: the loss is forgetting, not missing the last shard.
- **P5.** |P − A| ≤ 5 pp.

## Decision rule

| result | what happens |
|---|---|
| Pilot gap F − A < 10 pp | Stop after the pilot: **uninformative**, nothing recoverable. |
| Main run: F − A < 10 pp, or McNemar p ≥ 0.05 for F vs A | The positive control failed in the session that matters: **uninformative**. B − A is reported, and nothing is decided on it. |
| Control holds, and all of these hold: (i) B − A > 0 with exact McNemar p < 0.05; (ii) task-level sign-flip p < 0.05; (iii) B − A > \|P − A\| when P ran; (iv) B's no-code rate ≤ A's + 3 pp; (v) B's cost per success ≤ 1.2 × A's | **Candidate** for the `interactive` L1 module (plan §5.1, §7 S2). Adoption is the coordinator's, in a separate PR. |
| Control holds, B − A < 0 with McNemar p < 0.05 | **Harmful** on this protocol; recorded, not adopted. |
| Anything else | **Null**, published with its interval; not adopted. |

**Stop rules.**
- The pilot gap rule, above.
- More than 10% of conversations halted in any condition (checked after at least 10): the run stops.
- Cumulative spend (pilot + main) reaches US$ 1.40: no new units start. In-flight units finish, at about US$ 0.05 at most, under the US$ 1.50 cap.
- A design problem on the first launch: stop, amend this file, commit, relaunch, and discard the partial data (the rules file, step 2).

## Budget

- Pilot: about 190 calls, ≈ US$ 0.10.
- Main: at k = 5 with P, 160 units × 16 calls ≈ 2,560 calls; `pilot_decision` sizes it to the measured cost.
- Price: DeepInfra list price for this model, read from OpenRouter's public endpoint list on 2026-09-25: US$ 0.06/M in, 0.18/M out.

## PROTOCOL.md, rule by rule

| rule | how it is met |
|---|---|
| §1 wall | The hidden tests live only in `tasks.py` and the grader process; the model has no tools and sees only the shards. |
| §2 halts | Defined above; halted pairs leave the comparison. |
| §3 cache and route | Route pinned. `cache_read_tokens` recorded per call. Cost is priced conservatively. |
| §4 interface | No tool calls. The extraction preflight runs on synthetic replies (`--check`), and the pilot's no-code count is read before the main run. |
| §5 judge floor | No judge: deterministic tests. |
| §6 placebo | Arm P, when the budget allows (the n rule ranks it above replicas 4–5). |
| §7 split | Nothing is fitted. |
| §8 replicas | k is chosen as above, and the task-level test is reported beside McNemar. |
| §9 binarised view | Pass-all is the registered outcome. Per-test results are stored, so a continuous view can be recomputed without re-running. |
| Standing: reproduce a known number | There is no published number for this corpus. The positive control (F > A, in the same session) is the check that the instrument shows the paper's effect. |

## What this cannot show

- **How chat actually sends history.** `ChatSession` today does NOT send a multi-turn message list: it flattens the last six turns into one user message ("Conversation so far: User: … Assistant: …"). This arm measures a real message list, which is how the Code screen's `CodeSession` sends history, not chat, the TUI or Discord (plan §7 S2: moving chat to real history is a separate, measured change). How the recap behaves on the flattened form is not measured. Beyond six turns the flattened form drops whole turns, and no recap can list what is no longer in the prompt.
- **Tools.** The shipped prompt tells the model to use its tools. Here it has none, and answers with code in text. The tool path, writing the function to a file, is not measured.
- **Real users.** They answer questions, rephrase, contradict themselves and stop early. The simulated user does none of these.
- **Other models, providers, days or temperatures.** One of each.
- **Other task kinds, and longer sessions.** Only code tasks are covered, with 5-turn conversations. Writing, analysis, tens of turns, and compaction are not.
- **The per-install layers.** Owner identity, AGENTS.md and skills are absent.
- **The paper's harness-side RECAP and SNOWBALL.** Not tested.
- **Whether listing the requirements is what helps,** if B wins. The placebo separates "more text in that slot" from the recap. It does not separate "a list" from "any restatement".
