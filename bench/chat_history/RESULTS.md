# Results: chat history, flattened against real messages

2026-09-25. Pre-registration: `PREREGISTRATION.md` (commit `d86e8efb`), for the product change in `d4ba6f94` (`CHIMERA_CHAT_REAL_HISTORY`, off by default). Data: `results/prefix.json`, `results/cache-live.json`, `results/run.json`, `results/report.txt`.

**Spend: US$ 0.314 measured** (A2 US$ 0.021, B US$ 0.293), against a US$ 1.50 cap. No halts, no stop rule fired, no amendment.

## The decision

**Do not flip the default.** Under the registered rule, two conditions fail independently:

| condition | result | holds |
|---|---|---|
| Positive control | NOHIST 1/32 = 3.1% against FLAT replica 0 30/32 = 93.8% | yes |
| Standing check | REAL replica 0: 28/32 = 87.5%, against H7's SHARDED-A 29/32 = 90.6% | yes |
| **Non-inferior** | REAL − FLAT = **−3.9 pp**, task-cluster bootstrap 95% **[−10.2, +1.6]**; lower bound below −10 | **no** (by 0.2 pp) |
| Not harmful | upper bound > 0; McNemar p = 0.30 | yes |
| Guards | no-code 1 vs 2; US$ per success 0.00122 vs 0.00121; no halts | yes |
| **Cache improves** | A1: REAL leaves 9% **more** characters uncacheable; A2: REAL has **more** uncached tokens in both orders | **no** |

`run.py --report` prints **"NOT SHOWN NON-INFERIOR: do not recommend flipping"**. The cache condition would have given "no reason to flip" on its own. This is a null on success and a registered fail on cache. It is not a finding of harm: the −3.9 pp sits inside the replay floor (Part B), and the test that could call it harm is far from it.

**What decides it is the cache, not the success result.** Even a clean non-inferiority would not have been enough, because the six-turn window as specified undoes the cache gain once a conversation passes seven turns (A1). That result is deterministic, and it is structural.

## Part A — cache

### A1, offline (deterministic, US$ 0)

The frozen ten-turn chat conversation through a real `ChatSession` and `Agent`, 17 requests per arm, rendered as a chat template lays them out.

| | FLAT | REAL |
|---|---:|---:|
| characters sent | 56,428 | 65,723 (+16%) |
| characters reusable | 37,790 | 45,395 |
| **characters not reusable** (primary) | **18,638** | **20,328 (+9%)** |
| reusable share | 0.670 | 0.691 |

**Registered result: REAL leaves more characters uncacheable, not fewer.** Its reusable share is higher, but it sends 16% more, because the earlier turns' tool calls and results stay in the history, and the share gain does not cover that.

The first request of each turn is the one a new turn pays for. Characters that request could **not** reuse:

| turn | FLAT | REAL | |
|---:|---:|---:|---|
| 2 | 744 | 1,229 | |
| 3 | 1,051 | 1,084 | |
| 4 | 1,332 | 1,407 | |
| 5 | 1,619 | 1,080 | REAL ahead |
| 6 | 1,888 | 1,225 | REAL ahead |
| 7 | 2,209 | 1,087 | REAL ahead |
| 8 | 2,181 | 3,348 | the window slides |
| 9 | 2,178 | 3,345 | |
| 10 | 2,182 | 3,186 | |
| **turns 2–7** | **8,843** | **7,112 (−20%)** | |
| **turns 8–10** | **6,541** | **9,879 (+51%)** | |

Three mechanisms, each visible in the rows:

1. **Inside the window, REAL's cost per turn is flat and FLAT's grows.** FLAT re-sends the whole replay after the clock line on every turn, so what it cannot reuse grows with the conversation (744 → 2,209). REAL re-sends only the newest exchange, about 1,100–1,400 characters whatever the length. From turn 5 on, REAL is ahead.
2. **The six-turn window slides by one turn every turn from turn 8.** The history then starts with a different turn each time, so the first request of turns 8, 9 and 10 reuses the system message and nothing else: 1,414 characters, against FLAT's 1,669 (FLAT also shares the head of its turn context, up to the clock). Every earlier turn is re-sent uncached, on every turn, for as long as the conversation lasts.
3. **A one-turn lag.** The loop sends this turn's message with its turn context and keeps it bare in the transcript, as designed, so nothing volatile is stored. The next turn's history therefore diverges from the previous request at the previous user message. What a turn reuses is the system message plus every turn *before* the previous one: on turn 2 that is the system message only (1,414 against FLAT's 1,669).

Prediction P3 is partly wrong. The per-turn shape was predicted (the gain inside the window, and turns 8–10 back to the system message, below FLAT), but the registered total went the other way. The extra 16% of history text and the slide outweigh the gain on a ten-turn conversation.

### A2, live (pinned DeepInfra, both orders; US$ 0.021)

Leaving each arm-run's first request out, as registered:

| order | arm | uncached tokens (primary) | cached share |
|---|---|---:|---:|
| FLAT → REAL | FLAT | 16,738 | 0.791 |
| FLAT → REAL | REAL | 21,152 | 0.746 |
| REAL → FLAT | REAL | 18,064 | 0.783 |
| REAL → FLAT | FLAT | 8,514 | 0.894 |
| **pooled** | FLAT | **25,252** | 0.842 |
| **pooled** | REAL | **39,216** | 0.765 |

**Registered result: REAL has more uncached tokens in both orders.** P4 (5–25% fewer) is wrong.

**What the live instrument could and could not see.** The rows (`cache-live.json`) show that most of the gap is not the mechanism:
- The provider **missed completely** (0 cached tokens on a ~5,000-token prompt that had been cached a moment earlier) on 2 of FLAT's 16 requests in the first order, 3 of REAL's in the first and 2 in the second, and 0 of FLAT's in the second. One miss costs about 5,000 uncached tokens, which is larger than anything the history form moves.
- On the requests where the cache answered, uncached tokens per request were 463 and 532 for FLAT, and 472 and 601 for REAL: the same order. *This split is exploratory, not registered.*
- The systematic part is the one A1 shows. When the cache answers, REAL's cached tokens on a turn's first request climb from 4,352 to 4,608–4,864 on turns 4–7 and fall back to 4,352 once the window slides; FLAT's stay at 4,352. On prompts of about 5,000 tokens, 4,400 of which are tool schemas cached by both arms, the difference is a block or two of 256 tokens.

So the live run cannot resolve the mechanism at this length; misses dominate it. It does agree with A1 in sign, and it is registered as a fail. Neither half of the cache condition holds.

## Part B — success on the sharded corpus

32 tasks × 4 replicas × 2 arms, through a real `ChatSession`, pinned DeepInfra. 128 of 128 units finished, with 0 halts and 0 empty replies.

| arm | pass | Wilson 95% | no-code | US$ | US$ per success | prompt tokens | cached | final-turn out |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| FLAT | 119/128 = **93.0%** | [87.2, 96.3] | 2 | 0.144 | 0.00121 | 856,186 | 327,936 (0.383) | 1,058 |
| REAL | 114/128 = **89.1%** | [82.5, 93.4] | 1 | 0.139 | 0.00122 | 763,021 | 328,960 (0.431) | 1,117 |
| NOHIST | 1/32 = 3.1% | [0.6, 15.7] | 25 | 0.010 | — | 19,584 | 8,192 | 1,602 |

**Paired.**
- Task level (primary): mean −3.9 pp, cluster bootstrap 95% [−10.2, +1.6]; 32 tasks, sign-flip p = 0.34.
- (task, replica) pairs: 128 pairs, diff −3.9 pp, Newcombe [−10.3, +2.2]. FLAT alone passed 10 pairs and REAL alone 5, exact McNemar p = 0.30.

**Floors.**
- Replay: replicas of one task disagree pairwise 9.9% of the time under FLAT (6 of 32 tasks mixed) and 12.5% under REAL (7 of 32).
- A 3.9 pp gap is the size one replica flip in either direction moves. The instrument does not separate it from zero.

**Where the pairs differ.**
- Ten tasks are not 4/4 in both arms. REAL is behind on 5 of them:
  - `t02_slugify` 4 vs 2;
  - `t04_roman` 4 vs 2;
  - `t25_calc` 4 vs 2;
  - `t18_parse_query` 4 vs 3;
  - `t11_parse_date` 1 vs 0.
- REAL is ahead on 3: `t01_parse_duration` 3 vs 4, `t07_flatten` 3 vs 4, `t29_split_csv_line` 2 vs 3.
- The rest are ties.
- Most failures in both arms miss one stated requirement, usually the last shard's error case (`raises(ValueError, …)`): the same kind of miss H7 read by eye.
- Three conversations, two FLAT and one REAL, all on `t29_split_csv_line`, are "no-code" because the reply's only block does not parse (`SyntaxError` at extraction). That is an instrument limit, the same in both arms and recorded as a fail in both, as registered.

**Predictions.**
- P1 held: both arms passed at least 80%.
- P2 held on the point: |−3.9| ≤ 5 pp. Non-inferiority missed by 0.2 pp.
- P5 held: NOHIST passed 3.1%.
- The standing check reproduced H7's number within 3 pp: REAL replica 0 at 87.5% against 90.6%.

**One more difference of the change, visible here and not registered as an outcome: skills.**
- Under FLAT, skill retrieval reads the whole flattened block, code included, and a skills block was injected on **640 of 640** calls.
- Under REAL it reads the new message only: **380 of 640**.
- That is why REAL sent 11% fewer prompt tokens and left 18% fewer uncached (434k against 528k) in this tool-less, five-turn setting, the opposite of A.
- Whether the skills also moved success cannot be separated here. It is part of the change, as the pre-registration says.

## If this is reopened

The code stays in, off: `CHIMERA_CHAT_REAL_HISTORY=1` turns it on for an owner who wants earlier tool calls kept, and the tests pin its behaviour. To give the coordinator a reason to flip it, the cache half has to change first, and it can be checked offline at US$ 0 before any paid call:
- **Trim in blocks instead of by one turn.** For example, when the window would pass six turns, cut back to three. The history then starts at the same message for several turns, and the slide in A1's turns 8–10 happens once per block instead of every turn. `measure_prefix.py` can show it deterministically: re-run it with a longer `TURNS`.
- **Accept or remove the one-turn lag knowingly.** It comes from keeping the stored transcript free of the turn context. The alternative, storing each message as it was sent, puts stale facts into history, which the plan rules out.
- **Measure the half that motivated the change with tools.** A later turn still seeing what an earlier one read and ran. That needs a sharded task set where the turns share tool state, which neither this bench nor H7's has.

## What this cannot show

- **The tool-call half of the change, for success.** Part B has no tools, so the benefit the change exists for is absent from it by construction: a later turn that still sees what an earlier one read and ran. A1 and A2 carry tool calls, but measure only the cache.
- **Longer conversations.** A1 has ten turns. Past turn 7 the slide costs REAL on every turn, so a longer thread widens the gap A1 shows. A Discord thread of forty messages is the case that matters here, and it is the one that favours FLAT.
- **Another trimming rule.** Only the registered six-turn window was measured. A window cut in blocks, or by tokens, would keep the prefix stable for several turns. Its numbers are not here.
- **Memory and profile placement, for success.** B has neither.
- **Resumed sessions, real users, other models, providers, days and task kinds.** One of each.
- **Small harms.** The success margin is −10 pp.
- **Money.** Tokens are counted; cached-token pricing differs by provider.
