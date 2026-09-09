# Chimera — Usage Guide

Chimera is a CLI-first, self-evolving agent with an LLM-Fusion reasoning core.
This guide covers installation, configuration, and every command with examples.

> New to the project? Read the [architecture overview](architecture.md) first.

---

## Install

Chimera uses [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Every command below is run as `uv run chimera <command>` (or just `chimera …`
once the project's virtualenv is on your PATH).

---

## Configure

Chimera is provider-agnostic via [LiteLLM](https://docs.litellm.ai/). Put your
keys and model choices in a local `.env` (it is git-ignored — never commit it):

```dotenv
# At least one provider key. OpenRouter unlocks 100+ models behind one key.
OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Tier-1/2 default model (single, cheap, must support tool-calling for Tier-2)
CHIMERA_DEFAULT_MODEL=openrouter/deepseek/deepseek-chat-v3.1

# LLM-Fusion: a diverse panel -> judge -> synthesizer
CHIMERA_FUSION_PANEL=openrouter/deepseek/deepseek-chat-v3.1,openrouter/openai/gpt-4o-mini,openrouter/meta-llama/llama-3.3-70b-instruct
CHIMERA_FUSION_JUDGE=openrouter/deepseek/deepseek-chat-v3.1
CHIMERA_FUSION_SYNTHESIZER=openrouter/openai/gpt-4o-mini
```

Other knobs: `CHIMERA_HOME` (state dir, default `.chimera`), `CHIMERA_LOG_LEVEL`
(`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, default off — caches identical
tool-free completions to skip repeated API calls), and `CHIMERA_AUTO_FUSE` (`on`/`off`,
default off — auto-fuses deep or **error-sensitive** turns in `solve`/`crew` without an
explicit `--fuse`; the cost-aware router still keeps cheap/tool turns single-model). The
router recognises exact-answer prompts (arithmetic, counting, digit ops) in the project's
main languages (en/pt/es/de/fr/zh/ja), so a critical short step gets fusion's protection
even when it is too short to trip the length gate.

**Providers, fallback & self-hosted.** Any LiteLLM `provider/model` slug works
(`openai/…`, `anthropic/…`, `gemini/…`, `ollama_chat/…`, `openrouter/…`, …). For a
self-hosted / OpenAI-compatible server (Ollama, vLLM) set `CHIMERA_API_BASE`
(e.g. `http://127.0.0.1:11434` with `CHIMERA_DEFAULT_MODEL=ollama_chat/llama3`). Use
`ollama_chat/` rather than `ollama/`: the `ollama/` prefix goes through Ollama's generate endpoint,
which cannot call tools. Set
`CHIMERA_FALLBACK_MODELS` (comma-separated) to fail over to another model if the
primary errors. In `chat`/`tui`, `/model <slug>` switches the model mid-session.

**Credential pools.** Give a provider several keys with
`CHIMERA_<PROVIDER>_KEYS` (e.g. `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). The
gateway rotates them round-robin across calls (spreading load / rate limits) and,
within a single call, fails over to the next key if one errors. A pool replaces
that provider's single `*_API_KEY`. *(OAuth/subscription logins — Copilot, Claude
Max, etc. — aren't wired yet; API keys and any LiteLLM-supported endpoint are.)*

Check everything is wired up:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Optional features.** Vision, Deliverable Mode and the Pet are built in. The rest
(web search, X search, image generation, TTS/voice, Spotify, browser) are pre-set
slots: fill the matching credential in `.env` (or install the dependency) and the
capability activates. `chimera features` is the live checklist. The `web_search`
tool (Tavily) auto-registers the moment `TAVILY_API_KEY` is set — and is the
template for adding the others (or use the MCP client / OpenAPI->tool importer).

> **Free vs paid models.** OpenRouter `:free` models cost nothing but are
> rate-limited upstream — fine for a quick `run`, flaky for multi-call commands
> like `fuse`/`solve`. For real use, a cheap paid model (e.g.
> `deepseek/deepseek-chat-v3.1`, fractions of a cent per call) is far more
> reliable.

---

## Commands

### Status — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — interactive multi-turn assistant (your right-hand)

An interactive REPL with conversation memory and tool use — the daily driver. It recalls relevant
long-term memory, threads the conversation across turns, and **saves the thread after every turn**
under `<home>/sessions`, so the next run continues where you stopped. `chimera sessions` lists the
saved threads; `chimera sessions --delete <id>` removes one.

```bash
uv run chimera chat                        # resume the newest thread; /exit to quit
uv run chimera chat --new                  # start a fresh thread instead of resuming
uv run chimera chat --session standup      # -s: resume, or name, one thread by id
uv run chimera chat --no-memory            # don't recall long-term memory
uv run chimera chat --cascade              # tiered routing: weak -> gate -> mid -> gate -> fusion
uv run chimera chat --fuse                 # fusion routing for tool-free turns (read the note)
uv run chimera chat --max-usd 0.50         # ceiling for the WHOLE thread, not for one turn
uv run chimera chat --write-region 'src/**,*.py'   # the only paths the file-writers may touch
uv run chimera chat --model MODEL --workspace DIR --max-steps 8
```

`--workspace`/`-w` roots the tools and is where `AGENTS.md` is read from; `--max-steps` caps the
tool-calling steps inside one message; `--model`/`-m` overrides the model slug — but see the note on
routing below.

Commands: `/help` · `/new` (fresh thread — the current one stays on disk) · `/reset` (same as
`/new`) · `/model <slug>` (no argument goes back to the default) · `/solve <task>` (hand it to the
verified loop) · `/exit` (also `/quit`, `/q`).

**It is governed, and it asks you.** `chat` and `assist` build the same stack the API path builds: a
taint ledger told your own message, the `<<external-data>>` fence around untrusted tool output, the
trust kernel, the `--write-region`, the owner's reach floor (`CHIMERA_REACH`) and the owner's
instructions from `agent.json`. The approver **prompts at your terminal** — this is the one surface
where somebody is guaranteed to be there to answer. Measured on the injection corpus
(`bench/right_hand_governance/RESULTS.md`, 2026-09-08): attacks blocked went from 0 of 7 to 7 of 7,
external reads returned inside the fence from 0 of 12 to 12 of 12, and over-block with a person
answering is 0.000 — at a cost of five questions across the eight legitimate rows. Piped
(`chimera chat < script.txt`) there is nobody to ask, so a question becomes a recorded refusal
rather than silent consent.

Honesty notes:

- **A refused tool call is printed under the reply** — `✗ run_shell did not succeed: …`. The model
  narrates around refusals: the measured case answered *"The command printed exactly: marker-42"*
  about a command the host-execution gate had declined. An approval gets its own line too
  (`governance: 1 approved this turn`), so a `y` you typed mid-turn leaves a trace once the reply
  scrolls away.
- **`--fuse` does not fuse a turn that carries tools, and a REPL turn always carries tools.** The
  router sends any tool-carrying turn to a single model, so in practice a `--fuse` turn here is a
  single-model turn. `--cascade` also wins over `--fuse` when both are given. The one route in the
  terminal that really fuses is `assist`'s `/task`.
- **Naming a model pins it, and the tier ladder steps aside.** Under `--cascade` the ladder is what
  chooses a model per turn, and it used to swallow the slug you named without a word. Now `--model`
  and `/model <slug>` win for as long as one is named — a line says the ladder is off — and
  `/model` with no argument hands the job back to it.
- Every turn prints its tokens and price — `cost: unavailable` when the model's list price is
  unknown, never a guessed zero — and appends a row to `<home>/usage.jsonl`, which is what the
  desktop app's Cost screen reads.
- **`--max-usd` bounds the thread, not the turn.** One meter runs from the first message to
  `/exit`; `/solve` draws on the same money; and once it is spent the next message is refused
  before it is sent, rather than paying a call to discover there was nothing left. A reply that
  was cut short — by the ceiling, by `--max-steps`, or by a context that stopped fitting — says so
  on its own line, because a truncated answer otherwise reads exactly like a finished one.
- **A restored turn says so.** When a thread comes back off disk, a dim line under the reply counts
  the replayed turns that were restored and how many of those had their provenance never recorded.
  Those are replayed inside the data fence rather than as the model's own words, and until now the
  fence was invisible to the person it protects.
- **`/solve <task>` hands the conversation to the verified loop** — plan, edit, verify, and revert
  the attempt when it fails, which is the second of the two buttons the desktop's code screen has.
  It never starts by itself, it prints the task and the ceiling before it runs, and the loop's own
  answer is recorded in the thread. With no argument it takes the last thing you asked.
- **MCP servers reach the terminal.** With `CHIMERA_MCP_AUTOLOAD=1` the servers in `mcp.json` are
  mounted before the fence, so the denylist, the kernel and the taint ledger cover them and a
  server's output arrives inside the data fence like any other external read. They are connected
  once per process and shared with the app, so nothing is spawned twice.
- `/reset` **starts a new thread**; it does not erase the current one. It cleared an in-memory
  transcript back when nothing was on disk; now that the thread is a file, clearing it in place
  would destroy work. (In `assist` and `tui`, which persist nothing, `/reset` still clears context.)
- `chimera doctor` reports where the agent's commands run and whether it asks first: the configured
  sandbox, whether an OS sandbox is actually available, and the host-execution posture.

### `assist` — the same right-hand, cheap by default

`assist` is `chat` with the second-brain defaults on: the tier cascade routes chit-chat to cheap
models and escalates the hard asks, your persistent profile (`chimera profile`) is the stable
preamble, and memory, nudges and end-of-session consolidation are active. On exit it prints a
session receipt — tier distribution and measured tokens — so "cheap by default" is a number.

```bash
uv run chimera assist                      # cascade, profile and memory on
uv run chimera assist --no-cascade         # one default model instead of the ladder
uv run chimera assist --no-memory          # don't recall long-term memory
uv run chimera assist --max-usd 0.25       # ceiling for the WHOLE run, not for one turn
uv run chimera assist --write-region 'src/**'      # the only paths the file-writers may touch
uv run chimera assist --model MODEL --workspace DIR --max-steps 8
```

Commands: `/help` · `/task <hard ask>` (full-power fusion, one shot) · `/solve <task>` (hand it to
the verified loop) · `/profile <kind>: <fact>` (remember something about you — kinds: `preference`,
`project`, `context`, `name`) · `/model <slug>` · `/reset` (clear the conversation context; nothing
is deleted) · `/exit` (also `/quit`, `/q`).

Governed exactly as `chat` is — same registry, same prompting approver, same refusal, governance and
cost lines, same `usage.jsonl` row, same MCP servers, same `--max-usd` meter over the whole run, and
the same `/solve`. Two differences worth knowing: **`assist` keeps no thread** (it forgets on exit —
use `chat` for a conversation you want back); and naming a model pins it, which turns the tier
ladder off for as long as it is pinned — the ladder is what chooses a model, and it used to accept
the slug and ignore it.

`/task` runs one forced fusion on the ask alone: the conversation is not sent to the panel, on
purpose, because feeding the thread to a panel plus a judge plus a synthesizer multiplies the cost
of the route that exists to be used sparingly. Its answer *is* part of the next turn's context now,
and it prints a price and writes a `usage.jsonl` row like every other turn — the most expensive
route in the terminal was the one the Cost screen could not see.

### `tui` — full-screen terminal app

A Textual full-screen UI over the same conversational core. Two panes: a **conversation log** that
renders replies as Markdown (fenced code is syntax-highlighted), with the model's tokens **streaming
in live** as they arrive; and an **activity panel** showing what the agent did this turn — the tools
it called, the token count and cost, and how many memory facts were recalled.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
uv run chimera tui --model MODEL --workspace DIR --max-steps 8
```

Not the same flags as the REPLs. `tui` has `--stream`/`--no-stream`, which they do not have.
The `--cascade`, `--session`, `--new`, `--max-usd` and `--write-region` of `chimera chat` have no
equivalent here.

Commands: `/model <slug>` · `/reset` (clear context) · `/clear` (clear screen) · `/stream` (toggle
live tokens) · `/help` · `/exit` (also `/quit`, `/q`). Keys: `Ctrl+R` reset · `Ctrl+L` clear ·
`Ctrl+P` command palette · `PgUp`/`PgDn` scroll · `Ctrl+C` quit. Slash commands autocomplete as you
type.

Honesty notes:

- **The TUI is deliberately not governed.** It has the deployment allowlist and nothing else: no
  taint ledger, no `<<external-data>>` fence, no kernel, no approver. The reason is that its
  confirmation cannot be drawn — Textual owns the terminal, so the host-execution prompt is a
  question on a stdin nobody can reach. Measured in a pty, such a turn blocked for 123.8 s against a
  120 s timeout and came back as `✗ run_shell` with no explanation
  (`bench/right_hand_governance/RESULTS.md`, Part 2). Until it has a Textual-native modal, the seven
  attacks `chat` blocks still execute here — prefer `chat` or `assist` when a refusal matters.
- It persists nothing: closing the TUI ends the conversation. `chat` is the surface with threads.
- Token streaming is the single-model path only — under `--fuse` (a panel→judge→synthesizer turn)
  there are no incremental tokens, so the panel shows a "synthesizing" status rather than a fake
  cursor. That label follows the flag and not the route: as in `chat`, a turn carrying tools does
  not fuse, and a REPL turn always carries tools.
- Cost reads "unavailable" when the model's list price is unknown (never guessed), and each turn is
  appended to `<home>/usage.jsonl` like the REPLs'.
- There is no verify/revert here, and no MCP. Both went to `chat` and `assist`, which answer `/solve`
  and mount the configured servers, because both rest on the taint ledger and the approver that this
  surface has decided not to have. Verify-or-revert also runs in `chimera solve` and `chimera project`.
- If Textual isn't installed, `tui` falls back to the plain `chat` REPL, passing every argument
  explicitly so the fallback survives its first turn. Streaming has no meaning there, and the thread
  is saved like any other `chat` thread.

### `serve` — messaging gateway (HTTP or Discord)

Exposes the agent with one conversation (and its memory) **per chat**. The routing core is
transport-agnostic; adapters plug in.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Each `chat_id` keeps its own context, so different users/threads don't mix.

**Unattended operation (webhooks).** Register a job that fires on an inbound HTTP POST, so
Chimera runs without anyone typing — a GitHub push, a Stripe event, a cron-as-a-service
ping:

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

The POST body is handed to the job's task as context, and every job registered for that
hook runs. `GET /health` and `POST /chat` still work alongside it.

**Native Discord.** Run Chimera as a Discord bot — each channel is a session, and the agent
can also send messages via the `send_message` tool:

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Create the bot at <https://discord.com/developers>, enable the **Message Content** intent,
and invite it to your server. It replies in any channel it can see (filtered to ignore its
own and other bots' messages). The token is read from the environment — never hard-coded.

**Native Telegram.** Same adapter pattern, and it needs **no extra dependency** (the
Telegram Bot API is plain HTTP):

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Native Slack.** Receives via Socket Mode (needs the `messaging` extra) and sends via the
Web API. Enable Socket Mode on your Slack app to get an app-level token:

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (send).** WhatsApp is *push-based* (messages arrive at a Meta webhook you host),
so unlike the others there's no connection to open. Set the Cloud API creds and the agent
can **send** WhatsApp messages via the `send_message` tool in any `serve` mode:

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**Two-way WhatsApp.** Point your Meta app's webhook at `https://<your-host>/whatsapp` and set
`CHIMERA_WHATSAPP_VERIFY_TOKEN` (any string you choose, matching the app config). `chimera
serve` then verifies the subscription (`GET /whatsapp`) and routes inbound messages
(`POST /whatsapp`) through the gateway, replying over the Cloud API. WhatsApp still needs a
public URL for the webhook — that's the only part outside Chimera.

**Native Signal (two-way).** Signal has no official API, so Chimera talks to a
[`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api) bridge you run
(Docker) and link to your number — plain HTTP, no Python dependency:

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1, single-shot completion

A single model call, no tools, no fusion. Cheapest path.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Vision / image paste.** Attach images with `--image` (a path or URL, repeatable)
— needs a vision-capable model:

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Deliverable Mode (produce an artifact)

Where `run`/`chat` answer conversationally, `deliver` produces a complete,
self-contained document (report, plan, spec, README...) and writes it to a file.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — the raw ReAct tool-calling loop

Thought → Action (tool) → Observation, until a final answer. Tools are scoped to
the workspace.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (the differentiator)

Runs a *panel* of models, a *judge* analyzes their answers
(consensus / contradictions / blind spots), and a *synthesizer* writes the final
answer. Use `--show-panel` to see the full trace.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

Fusion is ~2-3× the cost of a single call, so reserve it for hard reasoning. `fuse`
also prints the per-stage token cost (panel / judge / synth) so you can see where a
run's tokens actually go.

**Selective fusion (ON by default, saves tokens).** The engine probes the first
`CHIMERA_FUSION_PROBE_K` panel models (default 2) and, when their answers agree closely,
skips the rest of the panel *and* the judge — synthesizing straight from the agreeing
answers. The agreement check is a cheap local text comparison (no extra model call), so a
*disagreeing* turn escalates to the full pipeline and costs exactly the same as full fusion,
while an *agreeing* turn is cheaper. Tune the bar with `CHIMERA_FUSION_AGREEMENT` (0–1,
default 0.8), or set `CHIMERA_FUSION_MODE=full` (or pass `--full`) to always run the whole
panel + judge.

Why it is the default: across 3 runs of `chimera fusion-bench --tasks hard` (a paid
3-model panel) it cut tokens **~20–28%** and was correct on **every** turn it actually
short-circuited (16/16). Overall accuracy wobbled 0 to −8.3pp between runs, but that
variance lands entirely in the *escalated* bucket — where selective runs the identical
pipeline to full — so it is model nondeterminism, not a cost of early-stopping. Run the
bench on your own workload to see the trade-off for your panel and tasks:

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Pick reliable panel models.** Fusion only pays off if every panel member actually
> answers. Avoid OpenRouter `:free` model slugs in `CHIMERA_FUSION_PANEL` — they
> rate-limit (HTTP 429) under real load, and the panel silently shrinks to whatever paid
> model is left. A cheap, reliable trio: `openrouter/deepseek/deepseek-chat`,
> `openrouter/openai/gpt-4o-mini`, `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Skill cards (TRS reasoning cards, experimental)

The agent distils what it learns into **reasoning cards** — the five fields
Trigger / Do / Avoid / Check / Risk (plus retrieval keywords) — from both successes
(a *pattern* card) and recurring failures (an advisory *anti-pattern* card). When
`CHIMERA_SKILL_CARDS=on`, `solve` retrieves the top-k relevant cards (BM25 over
name + description + triggers) and injects them into the worker's reasoning context, so
the agent reuses what worked and avoids known failure modes. This closes the loop —
before, learned skills were stored and never read back.

Off by default: injecting cards adds prompt tokens, and TRS's *token* savings come from
shortening long reasoning traces, so on short-answer tasks the upside is accuracy, not
cost. This is not hypothetical — on the `hard` short-answer suite (paid deepseek-v3.1),
`skillcard-bench` measured cards costing **+290% tokens** and **−8pp accuracy** vs no
cards: with a near-ceiling model and no long trace to shorten, generic cards are pure
overhead that can distract. Enable cards for **long-reasoning** workloads (math/coding
with lengthy traces) where the token math flips, and always measure your own trade-off
first with a ground-truth check:

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

The bench reports accuracy with vs without cards, the token delta, the card hit-rate, and
accuracy split by hit/miss, with a PASS verdict when card accuracy stays within 1pp of the
no-cards baseline.

### Compact tool schemas (experimental)

Tool schemas — especially those imported from MCP servers or OpenAPI specs — carry
annotation noise (examples, titles, defaults, multi-sentence parameter prose, nested
request bodies) that is re-sent to the model on **every** ReAct step. With
`CHIMERA_COMPACT_SCHEMAS=on`, that noise is stripped and parameter descriptions trimmed
at advertise-time, **without** touching anything that affects a call (the function name
and description, and every schema's `type` / `properties` / `required` / `enum` are
preserved). The canonical schemas are untouched — only the copy sent to the model shrinks.

The saving is largest on verbose MCP/OpenAPI toolsets and compounds across every step;
native tools are already terse, so their reduction is small. Measure your toolset first
(no model calls — it just counts tokens):

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Off by default. Because compaction only removes annotation noise (never structure), the
only risk is the model having slightly less prose to pick a tool by — so it stays
conservative, and you should confirm tool-call behaviour on your workload before enabling.

### `solve` — Tier-2 autonomous (plan + verify-or-revert)

Plans the task, executes with the agent loop, then **verifies with an
executable command**. If verification fails, it reverts the workspace and retries
with feedback. The verifier (exit code 0 = success) is ground truth.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Useful flags:

| Flag | Meaning |
|------|---------|
| `--verify "<cmd>"` | command that must exit 0 (tests, a build, a linter) |
| `--workspace`, `-w` | where the agent reads/writes (default `.`) |
| `--max-attempts N` | verify-or-revert budget (default 3) |
| `--max-steps N` | tool-calling steps per attempt (default 8) |
| `--fuse` | produce the **plan** via fusion (deep reasoning) |
| `--guard` | gate every tool call through the governance kernel |
| `--no-plan` / `--no-manager` | skip the planning / review stage |
| `--rubric` | Manager judges via the **cascade rubric** (instruction-following → factuality → rationality) |
| `--no-remember` | don't auto-write a memory fact on success |
| `--no-evolve-skills` | don't auto-propose a learned skill when a task recurs |
| `--isolate` | run in a throwaway git worktree; changed files copied back only on success |
| `--require-diff` | an attempt that changed **no file** fails and is retried — for a code task, an explanation is not a fix |
| `--keep-workspace` | on failure, leave the last attempt's edits on disk instead of reverting — for when an **external** grader decides pass/fail |
| `--diff-feedback` | show a failed attempt its own reverted diff, framed as a path not to retake |
| `--stagnation-fuzzy` | match repeated-failure signatures approximately, so the anti-stall pivot fires on same-cause failures whose wording differs |

> **On `--max-steps`.** The default of 8 is tuned for small workspaces. On a **large repository it is
> the binding constraint**, not the model: SWE-bench run 1 scored an exact 0.0pp at 8 steps against a
> 250 MB checkout, and the same configuration at **30 steps** lifted the baseline's patch rate from
> 47% to 74% ([`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)). If the agent explores and
> then finishes without editing, raise this first.

> **`--require-diff` and `--keep-workspace` are for external grading.** `solve` is verify-or-revert:
> when *it* owns the pass/fail decision, reverting a failed attempt is right. When something else owns
> it — a CI job, a benchmark harness, a human reviewing the diff — `--keep-workspace` stops the agent's
> work being rolled back before that judge ever sees it, and `--require-diff` stops a confident
> explanation from being scored as a completed change. Both are **off by default**.

**`solve` learns across runs.** Each run feeds a closed behavioural loop, all gated by
verify-or-revert so only verified work has any effect: (1) relevant **lessons** from
past attempts (failures favoured) are folded into the plan/prompt, and a failed attempt's
**first faulty step** is localized and fed into the retry; (2) on a verified
success a deduped **memory** fact is written (recalled later by `chat`/`crew`); and
(3) when a task pattern recurs (≥ 2 prior successes), a reusable **skill** is proposed —
across the fusion panel and kept by cross-model **transferability** when `--fuse` is on —
and kept only if it passes governance validation and an executable smoke test.

### `crew` — Tier-3 multi-agent

A team of role agents collaborates on one task and a supervisor synthesizes the
final answer.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — SDLC crew (plan → build → test → review)

A pre-assembled software-lifecycle pipeline with **verify-or-revert** at the test
stage: `plan` decomposes the task, `build` implements it, `test` runs the verifier
(reverting and retrying the build on failure), and a reviewer critiques the result.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Each stage prints with a ✓/✗; the run is `success` only if the test stage's verifier passed.

### `meta` — agents building agents

Designs a specialized agent blueprint (name, tools, role prompt) for a task.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — governance verdict

Shows the trust kernel's decision (allow / warn / review / block) for an action.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — continuous-evolution benchmark

Measures whether performance *holds* over a chain of tasks (the anti-degradation
proof): overall pass rate, first-half vs second-half, longest streak.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

The report also carries a **statistically honest** degradation flag: rather than trusting
a bare first-minus-second-half subtraction (on a short chain a 0.2 swing is usually
noise), `degraded_significant` is only `1.0` when a Wilson confidence interval on the
drop excludes zero, `-1.0` when the sample is too small to say, and `0.0` otherwise —
plus the `degradation_ci_low/high` bounds. Separately, `CHIMERA_SKILL_ACCEPT_MODE=wilson`
gates the cross-model skill-accept decision on the *lower* confidence bound of the
transfer rate (so a lucky 2-of-3 pass no longer counts); default `point` keeps the raw
rate, since the Wilson bound is strict on tiny panels.

### `sandbox-bench` — state + side-effect grading

The text benches grade the model's *answer*; this one grades what the agent **did**. Each
task runs in an isolated sandbox dir, and the harness diffs the final file state against
the goal (any path allowed, outcome-style) **and** separately counts *harmful side effects*
— mutations outside the task's declared allowed set. So an agent that produces the right
result while clobbering an unrelated file is caught, not scored as a clean pass.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Reports `pass_rate` and `side_effect_rate`. It ships the *methodology* (a `StatefulTask`
with `goal_check` + `allowed` mutation set), not a large task suite — author tasks for your
own tools. The existing text-graders stay correct for pure-Q&A work.

### `memory` — curated long-term memory

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

Recall passes through an **admission gate** (a trust boundary): a recalled memory enters
the prompt only if it is relevant *and* free of override/injection text (memory-based
jailbreak defense). `memory prune` forgets under a budget by a multi-factor **value**
model (recency, specificity, kind, curation, reliability) — not a single cue.

The **graph layer** extracts `(source, relation, target)` triples from your memories
(`PassaPro uses Supabase`, `Alex prefers TypeScript`), so facts can be recalled by
entity, not only by keyword.

### `cron` — scheduled jobs & event SOPs

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — task board with worker lanes

A board (`backlog → doing → review → done`) where each card names a *lane* that
dispatches it to the agent stack: `solve` (Tier-2 autonomous, verify-or-revert) or
`crew` (Tier-3 role pipeline). The operational view of the loop the agent already runs.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` walks each card backlog → doing → done (success) or → review (needs attention).
`learn` reuses the cron-learner's recurrence detector to queue tasks the agent
repeats (deduped against the board) — schedule it to auto-fill the backlog.

### `workflow` — designed loops (Loop Engineering)

Author an autonomous loop as YAML instead of an ad-hoc prompt. Each step `uses` a
capability (`run` / `shell` / `solve` / `crew` / `lifecycle`), can be gated on the
previous step (`when: prev_succeeded | prev_failed`), and can loop (`repeat`, `until:
success`).

```yaml
# examples/workflow.yaml
name: build-and-report
steps:
  - name: build
    uses: solve
    with: { task: "Create greeting.py with greet(name)", verify: "python -c \"import greeting\"" }
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with: { prompt: "One-line changelog for greet()" }
```

```bash
uv run chimera workflow examples/workflow.yaml --workspace ./scratch
```

### `drift` — spec↔code drift gate

Keep a spec and the code aligned. A spec is a small YAML of requirements
(`defines` a symbol / `contains` a regex / `absent` a regex / `command` exits 0). The
gate exits non-zero on drift, so it doubles as a verifier.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — import from another agent

Brings **config + skills** from Hermes or OpenClaw, and with `--apply` also
**merges long-term memory** (deduped, non-destructive). Default is a dry-run
preview.

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

The memory merge reports `{ADD, UPDATE, NOOP}` counts — duplicates become
`NOOP`, so re-running is safe.

### `evolve` — opt-in model evolution (advanced)

`chimera solve --collect` (on by default) logs each run as a trajectory. The
`evolve` commands turn those into training-ready datasets and a runnable LoRA
recipe. **Training is external and opt-in** — it changes model weights, so it
never happens automatically; Chimera prepares the data and a script and stops.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` accepts recipe knobs: `--min-steps N` keeps only long-horizon traces,
`--diverse` keeps at most one example per task (task diversity is the curation
bottleneck), and `--min-process P` (SkillCoach) keeps only traces whose *step-following*
score ≥ P — the fraction of tool steps that produced a successful, visible result — so a
lucky success that thrashed through failed tool calls isn't trained on. The per-step
events behind that score are captured automatically on every `solve` run; the filter is
off by default (`CHIMERA_SFT_MIN_PROCESS` sets a global default). `evolve tune` is
different from training — it runs a **meta-search** over the
agent *spec* (model, system prompt, step budget, panel, memory depth), scoring each
candidate on the daily scenarios and keeping an edit only on **non-regression**. It calls
models but never changes weights, so it is safe to run anytime.

Then, to actually train, on a GPU (or Colab): `pip install chimera-agent[train]` (or the
recipe's `requirements.txt`) and `python recipe/train.py`. Point `CHIMERA_DEFAULT_MODEL`
at the base model + adapter when serving.

### `pet` — a virtual companion

A persistent little companion whose stats drift while you're away. No key needed.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Tips

- **Tools vs reasoning.** Tool-calling turns always use a single model (fusion
  can't call tools); fusion is reserved for tool-free deep reasoning.
- **Inspect what happened.** `CHIMERA_LOG_LEVEL=DEBUG` surfaces routing and
  fusion-engagement logs.
- **Keep tests honest.** A good `--verify` command (a real test suite) makes
  `solve` reliable — it is the executable ground truth the agent is held to.
