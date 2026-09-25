# Results: the turn context lets a provider cache more of each request

2026-09-25. Study 25, wave 2 (`bench/PLAN-study25-system-prompts.md` §5.1 and §9).

- **Cost:** under US$ 0.01 over two live runs. That is about 297k prompt tokens and 96 output tokens on `openrouter/deepseek/deepseek-v4-flash-0731`, priced at 0.022 per million tokens in.
- **Raw data:** `results-live.json` and `results-live-pinned.json`, committed next to this file (token counts and provider names only).

## What was compared

The same six-turn Code-screen conversation was sent twice, through the real `Agent`. The model's replies were scripted, so both arms send the same conversation and the same tool calls. The only difference is where the per-turn notes sit: recalled facts, a finished job on turn 3, an approved plan on turn 4.

| Arm | Where the per-turn notes go |
|---|---|
| **before** | Appended to the system message, as `build_agent` did until this change |
| **after** | In the turn context at the head of the user message (`turn_context=True`), with the date and system facts added |

Each turn makes three tool calls and then answers, so each arm sends 24 requests.

## 1. The mechanism, offline (`measure_prefix.py`, deterministic, US$ 0)

This counts the characters each request shares with the one before it, which is what a prefix cache can reuse.

| | before | after |
|---|---:|---:|
| characters sent | 94,376 | 102,248 |
| characters reusable | 76,443 | 89,565 |
| **reusable share** | **0.810** | **0.876** |
| characters **not** reusable | 17,933 | 12,683 (**−29%**) |

The first request of each turn is the one a new turn pays for. Characters reusable on it:

| turn | before | after |
|---:|---:|---:|
| 2 | 1,535 | 1,442 |
| 3 | 1,532 | 2,215 |
| 4 | 1,531 | 2,976 |
| 5 | 1,531 | 3,736 |
| 6 | 1,535 | 4,513 |

- **Before, the reusable prefix stops where the first note begins,** about 1,530 characters in (the default system prompt), whatever the conversation holds.
- **After, it covers the system message and every earlier turn.** It grows with the conversation, which is where long sessions spend.
- **The price is the environment block,** about 330 characters per turn. It is why "after" sends 8% more characters in total and still leaves 29% fewer uncached.

## 2. The provider's own accounting, live (`replay_live.py`)

Both runs replay the requests above to a real provider and read `cache_read_tokens` back.
- Each request asks for one output token.
- Each arm starts with its own nonce, so one arm cannot warm the other's cache. This held for the system message but not for the tools; see run 2.

### Run 1: the default route, unpinned. **Read as confounded, not as a result.**

| | before | after |
|---|---:|---:|
| cached share | 0.446 | 0.576 |
| providers that served the 24 requests | 4 | 7 |

- An OpenRouter slug is a pool of endpoints, and each endpoint has its own cache.
- Consecutive requests landed on different providers, so this run measures the routing as much as the prompt.
- It is kept because it shows the confound, the same one `chimera-cache-confound-hosted-route` recorded for determinism.

### Run 2: one provider pinned (`--provider DeepInfra`, no fallbacks), with the default registry's tool schemas

| | before | after |
|---|---:|---:|
| prompt tokens | 122,424 | 124,896 |
| cached tokens | 109,568 | 117,248 |
| **cached share, all requests** | **0.895** | **0.939** |
| cached share, without each arm's first request | 0.929 | 0.941 |
| uncached tokens on the first request of turns 2–6 | 3,793 | 2,772 (**−27%**) |

**Cached tokens on the first request of each turn:**
- before: 4,352 on every turn;
- after: 4,352 → 4,352 → 4,608 → 4,864 → 5,120.

**What the numbers mean:**
- **The provider confirms the mechanism.**
  - Before, a new turn gets the tool schemas and the head of the system message from the cache (4,352 tokens, in 256-token blocks) and nothing after them.
  - After, what it gets from the cache grows with each turn.
- **A cross-arm effect, reported instead of hidden.** The "after" arm's first request found 4,096 tokens already cached. This provider serializes the tools before the system message, so before the nonce, and the "before" arm had just sent the same tools. The line without each arm's first request removes that.
- **Why the headline effect is small.** A six-turn conversation of about 5k tokens is short, and in a short conversation the tool schemas, which both arms cache, are most of every prompt. The gap grows with the length of the conversation: before, the uncached part of a new turn is the whole history; after, it is the latest exchange.
- **Within a run, the arms match.** Both are append-only, so steps 2 to 4 of a turn cache alike.

## What this does not show

- **Whether answers change.** The replies were scripted, so both arms saw the same conversation. Moving the recalled facts and the skills from the system message into the user turn could change what a model does with them. That is the success-parity arm the plan asks for. It needs a bench with a real model making the decisions, which costs money, and it is not run here.
- **Anything beyond one model, one provider and one six-turn conversation, from a single run.** The offline measurement is exact. The live figures are one draw each.
- **Money saved.** Tokens are counted, and cached-token pricing differs by provider.
