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
(`openai/…`, `anthropic/…`, `gemini/…`, `ollama/…`, `openrouter/…`, …). For a
self-hosted / OpenAI-compatible server (Ollama, vLLM) set `CHIMERA_API_BASE`
(e.g. `http://localhost:11434` with `CHIMERA_DEFAULT_MODEL=ollama/llama3`). Set
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

An interactive REPL with conversation memory and tool use — the daily driver.
It recalls relevant long-term memory and threads the conversation across turns.

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

The same conversational core powers the TUI and (upcoming) messaging gateway.

### `tui` — full-screen terminal app

A Textual full-screen UI over the same conversational core. Two panes: a **conversation log** that
renders replies as Markdown (fenced code is syntax-highlighted), with the model's tokens **streaming
in live** as they arrive; and an **activity panel** showing what the agent did this turn — the tools
it called, the token count and cost, and how many memory facts were recalled. Same flags as `chat`.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

Commands: `/model <slug>` · `/reset` (clear context) · `/clear` (clear screen) · `/stream` (toggle
live tokens) · `/help` · `/exit`. Keys: `Ctrl+R` reset · `Ctrl+L` clear · `Ctrl+P` command palette ·
`PgUp`/`PgDn` scroll · `Ctrl+C` quit. Slash commands autocomplete as you type.

Honesty notes: token streaming is the single-model path only — under `--fuse` (a panel→judge→
synthesizer turn) there are no incremental tokens, so the panel shows a "synthesizing" status rather
than a fake cursor. Cost reads "unavailable" when the model's list price is unknown (never guessed).
There is no verify/revert indicator here: verify-or-revert runs in `solve`/`project`, not in chat.
If Textual isn't installed, `tui` falls back to the plain `chat` REPL.

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
