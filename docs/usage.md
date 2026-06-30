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
(`INFO` / `DEBUG`).

Check everything is wired up:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
```

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

A Textual full-screen UI over the same conversational core: a scrolling chat log,
an input box, and a status bar. Same flags as `chat`.

```bash
uv run chimera tui
uv run chimera tui --fuse --no-memory
```

`/reset` clears context, `/exit` (or `Ctrl+C`) quits.

### `serve` — messaging gateway HTTP server

Exposes the agent over HTTP, with one conversation (and its memory) **per chat**.
This is the hub messaging adapters (Discord/Telegram, coming next) plug into.

```bash
uv run chimera serve --port 8765
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Each `chat_id` keeps its own context, so different users/threads don't mix.

### `run` — Tier-1, single-shot completion

A single model call, no tools, no fusion. Cheapest path.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
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

Fusion is ~2-3× the cost of a single call, so reserve it for hard reasoning.

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

### `crew` — Tier-3 multi-agent

A team of role agents collaborates on one task and a supervisor synthesizes the
final answer.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

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

### `memory` — curated long-term memory

```bash
uv run chimera memory add "Bruno prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
```

### `cron` — scheduled jobs & event SOPs

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
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
chimera evolve export --format sft --out d.jsonl   # curated SFT dataset (successes)
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
```

Then, on a GPU (or Colab): `pip install chimera-agent[train]` (or the recipe's
`requirements.txt`) and `python recipe/train.py`. Point `CHIMERA_DEFAULT_MODEL`
at the base model + adapter when serving.

---

## Tips

- **Tools vs reasoning.** Tool-calling turns always use a single model (fusion
  can't call tools); fusion is reserved for tool-free deep reasoning.
- **Inspect what happened.** `CHIMERA_LOG_LEVEL=DEBUG` surfaces routing and
  fusion-engagement logs.
- **Keep tests honest.** A good `--verify` command (a real test suite) makes
  `solve` reliable — it is the executable ground truth the agent is held to.
