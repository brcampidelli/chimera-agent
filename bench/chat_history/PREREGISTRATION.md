# Pre-registration — chat history: flattened against real messages

**Registered 2026-09-25, before any paid call.** Study 25, wave 2 gap (`bench/PLAN-study25-system-prompts.md` §7 S2/S3, §9 wave 2). Hard budget **US$ 1.50**; expected about US$ 0.35–0.50.

## Why

`ChatSession._assemble` builds every chat turn as one user message: the profile, the recalled facts, and the last six turns flattened to prose ("Conversation so far: User: … Assistant: …"). It is what `chimera chat`, `assist`, the TUI, the app's chat and both Discord paths send. Two consequences:
- every tool call of an earlier turn is gone by the next turn;
- nothing after the system message can be cached, because the block changes every turn.

The plan says moving to real message history is "a separate, measured change: cache hit rate plus a sharded multi-turn task set (2505.06120 protocol)". This is that measurement.

## The change under test

Commit `d4ba6f94` on this branch, behind `CHIMERA_CHAT_REAL_HISTORY` (`Settings.chat_real_history`, `ChatSession.real_history`), **default off**:
- the same six-turn window goes out as the model's own messages, tool calls and results included, each turn starting at its user message;
- the profile and the recalled facts move into the turn context (`Agent.run(turn_notes=)`), at the head of the new user message, which the transcript keeps bare;
- the session file does not change; a turn restored from disk replays as a user/assistant pair carrying the flattened form's label and data fence.

## Arms

| id | what the model receives each turn |
|---|---|
| **FLAT** | `ChatSession(real_history=False)`, the shipped default: system, then one user message (turn context, profile, facts, replay, message) |
| **REAL** | `ChatSession(real_history=True)`: system, the earlier turns as messages, then the turn context and the message |
| **NOHIST** | positive control, part B only: a fresh session given only the last user turn |

Everything else is identical between FLAT and REAL: model, route, temperature, system message, tools, corpus, user turns.

## Part A — cache

**A1, offline, deterministic, US$ 0** (`measure_prefix.py`).
- One ten-turn chat conversation, frozen in `TURNS`: ordinary requests (calendar, metrics, a note to the team, positions, a reminder), two recalled facts per turn, the owner profile rendered by `render_profile`, one fixed ~260-character reply per turn, and 1, 0, 2, 0, 1, 0, 2, 0, 1, 0 tool calls (`echo`) before the reply.
- Ten turns so that the six-turn window fills at turn 7 and slides for turns 8–10.
- The clock is pinned: two minutes per turn, fixed within a turn.
- Skill retrieval is off in both arms, so the only difference is the history form.
- Each request is rendered as a chat template lays it out (role, content, tool calls, call id). For each request, the characters it shares with the best earlier request of the same arm are what a prefix cache could reuse.
- **Registered metrics:** characters not reusable (primary), reusable share, and the reusable characters of each turn's first request, summed for turns 2–7 and for turns 8–10.

Disclosure: before this file was written, the script was run once with its numeric output discarded, to check that it runs and that each arm builds the message structure it claims (roles, window, turn context). No metric was read.

**A2, live** (`replay_live.py`).
- A1's exact requests, sent to `openrouter/deepseek/deepseek-v4-flash-0731` pinned to **DeepInfra** with no fallbacks, `max_tokens=1`, temperature 0.
- Each request carries the default registry's tool schemas plus `echo`, as a chat agent sends them.
- Each arm-run gets its own nonce at the head of the system message.
- Two orders, FLAT then REAL and REAL then FLAT, each arm-run with a fresh nonce. This provider serialises the tools before the system message (`bench/turn_context/RESULTS.md`), so an arm-run's first request can find the tools cached by the run before it; **every live metric leaves each arm-run's first request out**.
- **Registered metrics:** uncached prompt tokens (prompt − `cache_read_tokens`) summed over those requests (primary), and the cached share.

Uncached tokens are the primary cache metric, not the share, because REAL sends earlier tool calls that FLAT drops: its prompts are longer, and a share can rise while the full-rate bill rises too.

## Part B — success on the sharded corpus (`run.py`)

**Corpus, grader, user.** H7's, unchanged (`bench/sharded_recap`: 32 Python function tasks, 5 shards each, 328 hidden tests; reference 32/32, shard-0-only solution fails 112/128 later requirements).
- One shard per user turn; the fifth also carries H7's closing line, *"That is everything. Please give me the complete, final version of the code."*
- The simulated user is deterministic: turn *i* is shard *i*, whatever the model said.
- The final reply's fenced code runs against the hidden tests, in a child process, as in H7.

**The path.** Each conversation is a real `ChatSession` over a real `Agent`, built the way the chat surfaces build one, minus what varies per install:
- `turn_context=True`, as on every chat surface;
- no tools, no memory, no profile, no owner identity, no workspace;
- skill retrieval **on**, as shipped. Under FLAT the skills are retrieved against the whole flattened block, under REAL against the message; that difference belongs to the change and is recorded per call.

**System message.** `DEFAULT_SYSTEM_PROMPT` + blank line + H7's surface line (its Amendment 1: the bare prompt on a tool-less surface produced empty replies):
> No tools are available in this conversation, so no file can be created, read or run: write any code in your reply, in a fenced code block.

**Model.** `openrouter/deepseek/deepseek-v4-flash-0731`, pinned to DeepInfra, no fallbacks; temperature 0.2 (the `AgentConfig` default); no `max_tokens`; reasoning at the model's default.

**Units and order.**
- A unit is one (task, replica). Its arms start together, so they share minutes and route.
- `k = 4` replicas. Replica 0 of every task is submitted before replica 1 of any, so a stop leaves whole replicas.
- NOHIST runs on replica 0 only: 32 single-turn conversations.
- 8 units at a time.

**n.** The independent unit is the task, capped at the corpus's 32.
- At this project's ICC(1) ≈ 0.7 (PROTOCOL §8), four replicas carry about 1.27 observations per task-arm. They are bought to measure the replay floor (§2x), not for power.
- Non-inferiority is read on the task level. If per-task differences have an SD of 0.15–0.30, the 95% half-width is about 5–11 pp.
- The plan's default adoption margin (−2 pp, §8.9) is therefore out of reach on this corpus. The registered margin, **−10 pp**, only excludes gross harm, and with a true difference of zero the chance of meeting it is roughly 50–95% depending on that SD.

**Halts** (PROTOCOL §2). A conversation halts when the provider raises after the gateway's retries, a different provider answers, or a call is truncated at the ceiling. A halt is neither pass nor fail, and the halted pair leaves the comparison. An empty reply is not a halt: it is a product outcome, counted per arm.

## Metrics

- **Primary (success):** REAL − FLAT pass rate, as the mean over tasks of the per-task difference (over replicas where both arms finished), with a **task-cluster bootstrap 95% percentile interval** (20,000 resamples, seed 25).
- **Reported beside it:** Newcombe paired interval and exact McNemar over (task, replica) pairs; the task-level sign-flip test.
- **Positive control:** NOHIST pass rate against FLAT's replica-0 rate.
- **Standing check** (§2aa): REAL's replica-0 pass rate against H7's SHARDED-A, which is the same prompts sent as a message list (29/32 = 90.6%).
- **Floors:**
  - replay: disagreement between replicas of one task within an arm;
  - endpoint: pinned;
  - cache: `cache_read_tokens` per call.

  There is no paraphrase or placebo arm (PROTOCOL §6): no prompt text is under test, and the change is where existing text sits.
- **Guards (two-sided, §8.5):**
  - no-code rate;
  - empty replies;
  - US$ per success (list price, cache reads at the full input rate, the same in both arms);
  - prompt and cached tokens;
  - final-turn completion tokens;
  - calls that carried skills;
  - seconds per conversation.

## Predictions

- **P1.** FLAT and REAL each pass at least 80% (H7's SHARDED-A: 90.6%).
- **P2.** |REAL − FLAT| ≤ 5 pp, and non-inferiority holds.
- **P3.** A1: REAL has fewer non-reusable characters and a higher reusable share than FLAT.
  - On turns 2–7, REAL's first request reuses the system message and the earlier turns.
  - On turns 8–10 the window slides, and REAL's first request reuses about the system message only, no more than FLAT's.
  - FLAT's first request reuses the system message and the head of the turn context, up to the clock, on every turn.
- **P4.** A2: REAL's uncached tokens are 5–25% below FLAT's in both orders, and its cached share is 1–6 pp higher.
- **P5.** NOHIST passes at most 10%.

## Decision rule

The recommendation, to the coordinator, is to flip the default only if every condition holds. Nothing is flipped here.

| condition | test |
|---|---|
| **Control** | NOHIST rate ≤ FLAT replica-0 rate − 30 pp. If it fails, the instrument cannot show a loss of history, and success is **uninformative**. |
| **Standing check** | REAL replica-0 ≥ 75%. REAL sends what H7's SHARDED-A sent, so below it either the REAL path or the harness is broken. Nothing is recommended; the failures are read, and a dated amendment says which. |
| **Non-inferior** | lower bound of the task-cluster bootstrap 95% CI of REAL − FLAT ≥ −10 pp. |
| **Not harmful** | if the upper bound is < 0 and McNemar p < 0.05, the verdict is **harmful**. |
| **Guards** | REAL no-code rate ≤ FLAT's + 5 pp; REAL US$ per success ≤ 1.2 × FLAT's; halts ≤ 10% per arm. |
| **Cache improves** | A1: REAL's non-reusable characters < FLAT's; **and** A2: REAL's uncached tokens < FLAT's, in both orders. |

| outcome | verdict |
|---|---|
| all conditions hold | **recommend flipping** |
| non-inferior and guards hold, cache does not improve | **no reason to flip** |
| non-inferiority not shown, or a guard fails | **do not recommend** |
| harmful | **harmful**: do not flip |
| control fails | **uninformative** on success; the cache result is still reported |
| standing check fails | **do not flip**; an amendment says whether the REAL path or the harness broke |

The rule was exercised on synthetic rows before registration: identical arms with no cache gain read "no reason to flip", never "recommend"; a control that passes everything reads "uninformative".

`run.py --report` applies this table mechanically (`decide`).

## Stop rules

- Cumulative spend (A2 + B) reaches US$ 1.30: no new unit starts. In-flight units finish, well under the US$ 1.50 cap.
- More than 10% of FLAT or REAL conversations halted, checked after at least 10: the run stops.
- A design problem on the first launch: stop, amend this file, commit, relaunch, and discard the partial data.

## Budget

- A2: about 70 requests of about 5k prompt tokens each, one output token: about US$ 0.03.
- B: 256 conversations of five calls plus 32 single calls. H7 measured US$ 0.00093 per sharded conversation on this route: about US$ 0.30.
- Price: DeepInfra list price for this model (H7): US$ 0.06/M in, 0.18/M out.

## PROTOCOL.md, rule by rule

| rule | how it is met |
|---|---|
| §1 wall | The hidden tests live only in `tasks.py` and the grader's child process. The model has no tools and sees only the shards. |
| §2 halts | Defined above; halted pairs leave the comparison. |
| §3 cache and route | Route pinned; `cache_read_tokens` recorded per call; A2 measures the cache directly. |
| §4 interface | No tool calls in B. A2 sends tool schemas and earlier tool calls but reads only prompt accounting. |
| §5 judge floor | No judge: deterministic tests. |
| §6 placebo | Not applicable: no text is added. |
| §7 split | Nothing is fitted. |
| §8 replicas | k = 4 is priced above; the primary interval is task-level. |
| §9 binarised view | Pass-all is H7's registered outcome; per-test results are stored. |
| Standing: reproduce a known number | REAL's replica 0 against H7's SHARDED-A, as a registered check. |

## What this cannot show

- **The tool-call half of the change.** Part B has no tools, so the benefit this change exists for, a later turn that still sees what an earlier one read and ran, is absent from B by construction. Only A measures a history with tool calls in it, and only its cache accounting.
- **Memory and profile placement in B.** B has neither, so moving them into the turn context is measured for cache (A), not for success.
- **The window sliding under B.** Five turns fit inside the six-turn window. A shows what sliding does to the cache; nothing here shows what dropping whole turns does to success in either form.
- **Resumed sessions.** Restored turns (label and fence) are covered by tests, not by this bench.
- **Real users, Discord threads, other models, providers, days, temperatures and task kinds.** One of each, as in H7. The production bot's own traffic is not replayed.
- **Small harms.** The margin is −10 pp: a real loss of a few points would pass.
- **Money.** Tokens are counted; cached-token pricing differs by provider.
