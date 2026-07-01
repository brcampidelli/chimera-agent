<div align="center">

<img src="assets/logo-wide.png" alt="Chimera logo" width="460" />

# Chimera

**An open-source, self-evolving AI agent whose reasoning core is an LLM-Fusion engine.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-join-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><b>English</b> · <a href="README.pt-BR.md">Português</a> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

Chimera fuses **multiple LLMs per request** — a **panel → judge → synthesizer** pipeline
inspired by OpenRouter Fusion — instead of relying on a single frontier model, and it
**improves itself over time** (memory → skills → model) while resisting the
*continuous-evolution degradation* that limits today's agents.

> **Status:** early development (0.1.x). The full build plan (M0–M7) is implemented —
> Tiers 1–4 + the Fusion engine + multi-level self-evolution + a governance kernel —
> plus a **closed behavioural learning loop**, an **operational layer** (Kanban + worker
> lanes, SDLC crew, a declarative loop DSL), **execution isolation** (Docker sandbox +
> git worktrees), and the **paper techniques** it was designed around (HORIZON, VIBEMed,
> Spec Growth, AgentTrust v2, AutoMegaKernel, Meta-Agent, MOC).
> 332 tests (+ opt-in live integration) · `mypy --strict` clean · `ruff` clean.

---

## Why Chimera

Existing frameworks are each strong on one axis: Hermes/OpenClaw evolve skills but run a
single model; CrewAI/LangGraph orchestrate well but don't learn; TrustClaw/NemoClaw/ZeroClaw
bring security/sandboxing but don't evolve. **Chimera combines all four:**

- 🧬 **Fusion-as-reasoning** — the panel→judge→synthesizer engine is the reasoning core, not an add-on. The lift comes from the *synthesis* step itself, not only model diversity.
- 🪜 **Four capability tiers in one progression** — augmented tools → single-task autonomous → multi-agent teams → self-evolving ecosystem.
- ♻️ **A closed, multi-level self-evolution loop** that explicitly attacks continuous-evolution degradation (externalized state, drift-resistant context, verify-or-revert, an experience buffer fed back into planning).
- 🛡️ **A governance kernel that also self-improves** — allow/warn/block/review, with a statically-validated self-modification surface and guarded precedent.

## Features

**Reasoning & autonomy**
- **LLM-Fusion engine** — provider-agnostic panel of frontier + open models, a judge that surfaces consensus/contradictions/blind-spots, and a synthesizer; a **cost-aware router** fuses only when it pays (tool turns stay single-model).
- **Tier-2 autonomy** — plan → execute → Manager review (optionally via a **cascade rubric**, `solve --rubric`) → **verify-or-revert** (workspace snapshot/restore + a command verifier), with **git-worktree isolation** (`solve --isolate`) so edits only land when verified.
- **SDLC lifecycle crew** (`chimera lifecycle`) — a pre-assembled **plan → build → test → review** pipeline with verify-or-revert at the test stage.
- **Multi-agent teams** — role specialization, sequential & supervisor crews, MOC message consolidation, shared memory, parallel review. Crew roles can be **tool-using workers** (their own loop + tools), not just single-shot personas, and any agent can **`spawn_subagent`** (`solve --subagents`) to delegate a subtask to an isolated, tool-scoped subagent that returns only its result (no recursion, allowlist-bounded).
- **Parallel isolation** (`chimera solve-batch`) — solve many tasks at once, each in its **own git worktree**; non-conflicting edits merge back and files two workers both touched are flagged as conflicts, not clobbered. A crashing worker fails its unit, not the batch (`run_in_processes` adds a process/RPC boundary for fault isolation).
- **Context Explorer** (`chimera explore`, `solve --explorer`) — a FastContext-style isolated subagent that locates code by its own read-only `grep`/`glob`/read search and returns only a compact `file:line` evidence block, keeping the main agent's context clean. Runs on any (ideally cheap) model.

**Self-evolution & governance**
- **Closed behavioural loop** — past failures feed the planner (lessons), verified successes auto-write memory, and recurring tasks auto-evolve a validated, smoke-tested skill (proposed across the fusion panel and kept by cross-model transferability when fusion is on) — all gated by verify-or-revert; a failed attempt is pinpointed to its first faulty step on the retry. Plus a continuous-evolution benchmark and an EvoClaw naive-vs-guarded stress test.
- **Hierarchical memory** — working / episodic / semantic / persona **+ a graph layer** (`memory graph`) that recalls facts by entity; an optional **SQLite/FTS5** full-text backend (`CHIMERA_MEMORY_BACKEND=sqlite`); a **cross-session user profile** (persona facts applied every turn); **LLM consolidation** (`memory consolidate`) that merges near-duplicate facts; and **nudges** that suggest saving preferences you state in chat.
- **Opt-in model evolution** — `solve` collects trajectories; `evolve` curates SFT/DPO datasets and emits a runnable LoRA recipe, and **`evolve tune`** self-optimizes the agent spec (meta-search, kept on non-regression) against the daily scenarios. Training stays external/opt-in.
- **Governance kernel** — allow/warn/block/review (lexical rules + optional semantic judge, with rule distillation and a **guarded precedent store**), a static validator for the self-modification surface, an append-only audit log, governed tools, a **four-actor change model**, and a **spec↔code drift gate** (`chimera drift`).

**Providers**
- **Any model, one interface** — provider-agnostic via LiteLLM (100+ models through `provider/model` slugs); first-class keys for OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted & resilient** — custom endpoints for **Ollama/vLLM** (`CHIMERA_API_BASE`), **fallback chains**, **credential pools** with round-robin rotation, a live **`/model`** switch, and **prompt caching** (`CHIMERA_CACHE`) for repeated reasoning turns.

**Orchestration, interfaces & integrations**
- **Kanban + worker lanes** (`chimera kanban`) — a task board (backlog → doing → review → done) where cards dispatch to a `solve` or `crew` lane; `kanban learn` turns recurring tasks into cards.
- **Loop Engineering** (`chimera workflow`) — author an autonomous loop as YAML (steps that `use` the stack, with `when` conditions and `repeat`/`until` loops).
- **Interfaces** — a `chat` REPL, a full-screen **TUI** (Textual), and a **messaging gateway** (HTTP, or **native Discord / Telegram / Slack / Signal** via `serve --discord|--telegram|--slack|--signal`) with one conversation (and memory) per chat; the agent can also **send** messages via a `send_message` tool. **WhatsApp** works two-way via a Cloud API webhook (`POST /whatsapp`).
- **Execution sandbox** — run the shell tool locally or in an isolated **Docker** container (`CHIMERA_SANDBOX=docker`).
- **Integrations** — a first-class **MCP** client (stdio) + an **OpenAPI/REST → tool** importer; **crons + webhook triggers** (`serve` runs a task on an inbound `POST /webhook/<hook>` — unattended); **migration** of config/skills/long-term memory from Hermes Agent / OpenClaw.

**Built-in extras**
- **Reference tools** — batteries included: always-on `execute_code` (sandboxed Python), `code_interpreter` (stateful session), `arxiv_search`; config-gated `web_search`, `generate_image` (OpenAI), `text_to_speech` (ElevenLabs), `send_email`/`read_email` (SMTP/IMAP), `calendar_events` (`.ics`); and `youtube_transcript` (opt-in extra). Arbitrary REST services still plug in via the OpenAPI→tool importer.
- **Vision** (image paste), **Deliverable Mode** (polished artifacts), and a **Pet** companion — see all optional capabilities with `chimera features`.

## Quickstart

Requires Python **3.11+** (3.12+ recommended) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # set at least one provider key (OpenRouter recommended)
uv run chimera doctor       # check your environment
```

## Commands

```bash
chimera doctor / models / features    # status, configuration, optional capabilities
chimera chat                          # interactive multi-turn assistant (your right-hand)
chimera tui                           # full-screen terminal app (Textual)
chimera serve [--discord|--telegram|--slack]  # messaging gateway: HTTP, or a native platform bot
chimera run "PROMPT" --image pic.png   # single-shot Tier-1 (vision-capable with --image)
chimera deliver "a launch plan" -o plan.md   # Deliverable Mode: produce a polished artifact
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: panel -> judge -> synthesizer
chimera solve "TASK" --verify "pytest -q" --rubric --isolate   # Tier-2: verify-or-revert (+ cascade-rubric review), git-worktree isolated
chimera lifecycle "TASK" --verify "..."   # SDLC crew: plan -> build -> test -> review
chimera workflow flow.yaml             # run a declarative loop (Loop Engineering)
chimera crew "TASK" --mode supervisor  # Tier-3 multi-agent crew
chimera meta "an agent for X"          # Tier-4 meta-agent: design a specialized agent
chimera kanban add/board/run/learn     # task board with worker lanes (solve/crew)
chimera drift spec.yaml                # spec<->code drift gate (exit 1 on drift)
chimera memory add / graph             # curated long-term memory + entity-relation graph
chimera cron add / learn               # scheduled jobs (assigned + self-learned, confirmed)
chimera bench                          # continuous-evolution benchmark
chimera migrate hermes ~/.hermes --apply   # import config + skills + merge memory
chimera evolve status / tune / recipe   # opt-in evolution: spec meta-search (tune), SFT/DPO data + LoRA recipe
chimera pet new --name Chimi           # adopt a virtual companion
```

See the **[Usage Guide](docs/usage.md)** for install, configuration, and every command with copy-paste examples.

## Architecture

```
chimera/
  core/          agent loop (ReAct) + Tier-2 autonomy (plan, verify-or-revert) + git-worktree isolation
  fusion/        panel -> judge -> synthesizer + cost-aware router
  memory/        working / episodic / semantic / persona + graph layer + Memory Manager
  skills/        built-in library + skill-context retrieval
  evolution/     learned-skill evolver, auto-evolve hook, experience buffer
  governance/    trust kernel (rules + judge + guarded precedent), static validator, drift gate, four-actor model, audit
  orchestration/ roles, sequential/supervisor crews, MOC comms, SDLC lifecycle crew
  ecosystem/     meta-agent, change-tempo governance, trajectory collection, model evolution
  kanban/        task board + worker lanes (dispatch to crews / solve)
  workflow/      declarative loop DSL (Loop Engineering)
  tools/         native tools (files, shell, http)
  sandbox/       execution backends (local / docker-isolated)
  integrations/  MCP client (stdio) + OpenAPI->tool importer
  scheduler/     crons (assigned + self-learned) + SOP engine
  migration/     import from Hermes/OpenClaw (config, skills, memory-merge)
  providers/     LLM gateway (LiteLLM) — fallback, credential pools, custom endpoints, prompt cache
  interface/     conversational ChatSession (shared by chat, TUI, gateway)
  tui/  server/   full-screen Textual app · messaging gateway + HTTP transport
  eval/          continuous-evolution + EvoClaw stress test + daily scenarios
  cli/           the `chimera` command (CLI-first)
```

See [docs/architecture.md](docs/architecture.md) for the full design and the research it builds on.

## Roadmap

| Milestone | Status |
|---|---|
| M0–M7 — Tiers 1–4 + Fusion + self-evolution + governance | ✅ |
| M8 — Interfaces (chat/TUI/gateway), EvoClaw stress-test, opt-in model evolution | ✅ |
| Provider layer — self-hosted endpoints, fallback chains, credential pools, `/model`, prompt cache | ✅ |
| Closed behavioural loop — experience→planner, auto-memory, auto-skill (governed) | ✅ |
| Operational orchestration — Kanban + worker lanes, SDLC lifecycle crew, Loop DSL | ✅ |
| Execution isolation — Docker sandbox + git worktrees | ✅ |
| Paper techniques — HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |
| Paper techniques (II) — MemGate · multi-factor memory value · Data Recipes · OpenClaw-Skill · SkillAdaptor · DailyReport · OpenJarvis spec-search | ✅ |

Next: deeper continuous-evolution validation at scale, provider OAuth logins, and an
optional LangGraph durability backend. Model training (LoRA/DPO) stays external/opt-in by design.

## Development

```bash
uv run ruff check .      # lint
uv run mypy chimera      # type-check (strict)
uv run pytest -q         # tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Security issues: see [SECURITY.md](SECURITY.md).

## Community

Join the conversation on **[Discord](https://discord.gg/ACvBbrmguV)** — questions, ideas, and contributions welcome.

## License

[Apache-2.0](LICENSE).
