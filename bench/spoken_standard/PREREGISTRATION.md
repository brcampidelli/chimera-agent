# Pre-registration — H9: the spoken-output standard, said as what to do

**Registered 2026-09-25, before any paid call.** Study 25 (`bench/PLAN-study25-system-prompts.md`),
arm H9, situation S4. Budget: a hard cap of **US$ 0.30**, checked before every call against a
conservative price bound. Expected cost: about US$ 0.02.

## The question

When a Code-screen turn arrives by voice (`req.spoken`), `chimera/api/code_api.py` appends
`SPOKEN_NOTE` to the system prompt. It is the product's only output-format instruction. It exists
because of a failure: on 2026-09-17 the hands-free mode answered a spoken "are you understanding me?"
with four paragraphs, two lists and a rocket emoji, and the voice read all of it.

The note says what to do in one clause and then lists five things not to do: *no headings, no lists,
no tables, no code blocks, no Markdown, no emoji*. The plan's §2.3 cites a finding that naming a
forbidden word primes it: the named word was involved in 87.5% of violations (arXiv 2601.08070). The
plan's S4 asks for the talk layer's standard to be "said as what to do, not five 'no's".

This arm asks one question. **On the shipped model, does a rewrite that says what to do, instead of
listing prohibitions, produce more answers a voice can read as written?**

## Arms

Every arm uses the same system prompt, `DEFAULT_SYSTEM_PROMPT`, joined to the arm's note the way the
Code screen joins it (`f"{DEFAULT_SYSTEM_PROMPT}\n\n{note}"`). The prompt has no memory facts and no
workspace note.

| arm | note | length | role |
|---|---|---|---|
| **A** | `chimera.api.code_api.SPOKEN_NOTE`, imported, byte for byte | 494 chars, 100 words | the shipped standard |
| **B** | the rewrite below, frozen | 492 chars, 97 words | the candidate |
| **N** | none | — | positive control (see below) |

**B, frozen.** sha256 `00034da6bc8d7f8b2fd89489c980cc0d3bdc496e33f9b8df958ce786e50581e8`. `run.py`
refuses to run if the hash moves.

```text
The person said this aloud, and a voice will read your answer to them before they see it on a screen. Answer for the ear: the gist first, in two to four short spoken sentences of plain words a listener can follow. Say numbers, dates and versions as people say them. After the spoken part, put a line containing only --- and write anything exact below it (a plan, a list of files, code, a command, an ID, a path, a link): the voice reads what is above the line, and the screen shows all of it.
```

**What B changes, and what it does not:**

- It keeps the opening (reworded, same meaning), "two to four short sentences", and the `---`
  convention with the same closing clause.
- It drops the six "no" items. It does not name Markdown, emoji, headings or tables anywhere.
- It asks for "the gist first" always. A asks for it only when the answer overflows.
- It **adds** content A does not have:
  - numbers, dates and versions said the way people say them;
  - a longer list of what goes under the line (adding a command, an ID, a path and a link).

  This is a confound, and it is registered as one. B tests the rewritten standard as a package, not
  the do-versus-don't framing alone. The per-violation counts and the secondary outcome
  (`format_speakable`) are how the two are told apart. The priming mechanism predicts fewer
  Markdown and emoji violations. The added clause predicts fewer digit, path and URL violations.

## Setup

- **Model:** `openrouter/deepseek/deepseek-v4-flash-0731`, the product default.
- **Temperature:** 0.2, the agent default.
- **Reasoning:** off, as the voice mode asks for talk turns (`thinking=False`).
- **`max_tokens`:** 1500.
- **No tools.** A single `LLMGateway.complete` call with the system prompt and the request as the
  user message. The text answer is all that is measured.
- **Reasoning is passed explicitly.** It goes in as the `extra_body` that
  `LLMGateway._provider_kwargs(model, thinking=False)` builds. Found while building this arm:
  `LLMGateway.complete` accepts `thinking` and does not forward it. Only `stream_complete` does. The
  bench uses the non-streaming call so the serving provider is recorded on every row.
- **Order, interleaved per request:** A B B A on even-numbered requests and B A A B on odd ones,
  then N. An OpenRouter slug is a pool of endpoints (`CompletionResult.provider`), and interleaving
  keeps route drift from landing on one arm.
- **Endpoint:** the provider of every call is recorded and reported per arm. The endpoint is not
  pinned.
- **Pairs:** request × replica. The replica is the order of appearance within the arm, so A's first
  answer pairs with B's first answer.
- **Positive control N:** answered once per request.

## Corpus

`corpus.py` holds 36 requests, 18 in Portuguese and 18 in English. Each is written the way a
speech-to-text transcript gives it, and each can be answered with no tools or workspace.

| kind | n | what it tempts |
|---|---:|---|
| `files` — "list the files that…" | 6 | a bulleted list of file names and paths |
| `command` — "what's the command to…" | 6 | a code block, flags |
| `code` — "show me the code for…" | 5 | a fenced block |
| `url` — "which URL…" | 5 | a raw URL |
| `numbers` — numbers, dates, versions | 5 | long digit strings, ISO dates, dotted versions, addresses |
| `compare` — "compare X and Y" | 5 | a table |
| `chat` — plain chit-chat (control) | 4 | nothing; "Are you understanding me?" is the 09-17 request |

**Calls:** 36 × (2 A + 2 B + 1 N) = 180.

## The instrument: `checker.py`

The checker is deterministic, and it is unit-tested from both sides in `tests/test_spoken_checker.py`:
each rule has a case it must catch and a near miss it must not catch.

- **Where the spoken part ends.** It is cut exactly where the desktop reader cuts it
  (`screenPartStart` in `apps/desktop/src/lib/voice/speech-text.ts`, ported with its tests): at the
  first Markdown rule alone on its line. Everything under the line belongs to the screen and is not
  judged.
- **What counts as a violation,** by key:
  - **Markdown:** `heading`, `list`, `table`, `code_fence`, `inline_code`, `emphasis`, `quote`,
    `link`.
  - **`emoji`:** a pictograph or dingbat.
  - **`sentences`:** more than four sentences, counted per line the way the streaming reader
    cuts pieces.
  - **`url`:** a scheme URL, a `www.` URL, or a domain followed by a path.
  - **`digits`:** any of:
    - a run of seven or more digits;
    - three or more numeric groups, unless the token is a thousands-grouped number (an IPv4
      address still counts);
    - a hex id or UUID.
  - **`path`:** a rooted path, or an interior separator with two or more separators or a file
    extension.
  - **`empty`:** nothing is spoken at all.

  The exact rules and their reasons are in the module docstring.
- **Primary outcome — `speakable`:** zero violations above the line.
- **Secondary outcome — `format_speakable`:** the same, leaving out `url`, `digits` and `path`.
  This is the part of the standard both arms state.
- **Recomputed at report time.** Scores come from the stored answers, never from the run's own
  printout. A checker fix costs no calls (§2af). The checker's sha256 is written into the results
  file.

## Metrics, reported together

- **Per arm:**
  - speakable rate with a Wilson 95% interval;
  - format-speakable rate;
  - answers with each violation, and the counts;
  - the rate of using the `---` line;
  - spoken words (mean and median) and sentences (mean);
  - completion tokens;
  - truncations;
  - median latency;
  - priced cost;
  - providers.
- **Paired A versus B, on speakable and on format-speakable:**
  - a / b / c / d;
  - exact two-sided McNemar;
  - B − A with Newcombe's paired 95% interval;
  - a request-level sign test as the cluster check. The two replicas of a request are not
    independent, so the pair-level McNemar is anti-conservative. If the two tests disagree, the
    report says so.
- **Replay floor per arm:** the requests whose two replicas disagree on speakable. A difference
  between arms is read against it.
- **Per kind and per language:** speakable, per arm.

## Honest n

There are 72 pairs. Exact McNemar power at α = 0.05, computed by exact enumeration:

| p_d (discordant fraction) | 5 pp | 10 pp | 15 pp | 20 pp |
|---:|---:|---:|---:|---:|
| 0.15 | 0.12 | 0.50 | 0.97 | — |
| 0.20 | 0.10 | 0.39 | 0.81 | 1.00 |
| 0.30 | 0.08 | 0.27 | 0.58 | 0.87 |

The arm resolves effects of about 15–20 pp. **A 10 pp effect will most likely read as a null**, and
the report says so rather than "no effect".

## Predictions (written before the run)

- **P1.** A is speakable on nearly all `chat` requests (at least 7 of 8). A is least speakable on
  `files`, `url` and `numbers`.
- **P2 (the priming mechanism).** Markdown and emoji are rare in A, in under 10% of answers, because
  the note forbids them by name and this model follows it. So B has little room to move them. The
  B − A difference in format-speakable stays within the replay floor.
- **P3.** Any gain for B comes from `digits`, `path` and `url`, the clause A lacks. It concentrates
  in `numbers`, `url` and `files`.
- **P4.** B uses the `---` line at least as often as A.
- **P5 (positive control).** N is at least 20 pp less speakable than A.
- **Overall:** B − A on speakable lands between 0 and +15 pp. Under the rule below, a null is the
  more likely outcome.

## Stop rule

- The n is fixed. There is no interim look and no early stop on the result.
- The run stops only when the conservative spend bound reaches US$ 0.30.
- A call that fails or returns empty content is re-asked twice, then recorded as a halt. A halt is
  the route failing, not the model choosing a form, so it is not scored as an empty answer. A pair
  with a halt on either side is dropped and reported.
- If more than 10% of calls halt, the result is **inconclusive**.

## Positive control

- **The pipeline:** arm N (no note). If A is not at least 20 pp more speakable than N, then either
  the note does not reach the model or the checker cannot see a change of form. Either way, a
  comparison of A with B means nothing. The result is then **"no decision"**, whatever the McNemar
  says.
- **The checker:** its unit tests include the 09-17 failure shape (caught), plain answers in both
  languages (pass), and an answer that starts with the line (caught as `empty`).

## Adoption rule (frozen)

**B is adopted if and only if all of these hold:**

1. Speakable rises: B-only pairs outnumber A-only pairs, and exact two-sided McNemar p < 0.05.
2. The mean spoken word count of B is at most **1.20 ×** that of A.
3. The positive control passes.
4. At most 10% of calls halt.

Otherwise:

- If speakable falls with p < 0.05, the result is **"B worse"**.
- In every other case it is a **null**, and it is published.

Adoption here means a recommendation to the coordinator. **This PR does not change `SPOKEN_NOTE`**,
whatever the outcome.

## What this cannot show

- **Framing versus content.** B adds a clause about numbers, IDs, paths and links as well as
  dropping the prohibitions. The per-violation split shows where a difference comes from. It cannot
  show what a pure reframing, with nothing added, would do.
- **Other models, or the work model.** This is one model and one day. It covers the talk path
  (reasoning off). It does not cover the work path, with its thinking as configured, tools and a
  workspace.
- **Paraphrase floor.** There are no neutral rewrites of A (§8.1), so "any rewording moves it" is not
  ruled out. Only the replay floor is measured.
- **Audio.** This is text, not audio.
  - The checker judges what the model wrote, not what the listener heard. The reader already
    rescues some of it: it drops emphasis marks, keeps link labels, and reduces a URL to its host.
  - Time to first audio, which the plan pairs with H9, is not measured. The call is not streamed.
- **Real speech.** The requests are clean written sentences, not real speech-to-text output. There
  are no mis-hearings, no multi-turn history and no memory facts.
- **Checker coverage.** Some kinds of unspeakable text are not counted:
  - shell flags (`--soft`), symbols (→, ©) and bare file names are not counted;
  - the digit rules are one reasonable line, not the only one. The per-key counts let a reader
    redraw it.
