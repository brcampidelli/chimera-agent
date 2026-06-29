# Chimera

> An open-source, **self-evolving** AI agent whose reasoning core is an **LLM-Fusion** engine.

Chimera fuses multiple LLMs per request (a **panel → judge → synthesizer** pipeline, inspired by
OpenRouter Fusion) instead of relying on a single frontier model, and **improves itself over time**
(memory → skills → model) while resisting the *continuous-evolution degradation* that limits today's
agents.

It is built as a progression of four capability tiers on top of a transversal fusion engine:

| Tier | Capability | Key tech |
|---|---|---|
| **1 — Augmented Tools** | autocomplete, point fixes, script generation | contextual learning, RAG |
| **2 — Single-Task Autonomous** | end-to-end features, debugging, maintenance | planning + tool use, self-correction |
| **3 — Multi-Agent Teams** | coordinated swarms, full lifecycle | shared memory, role specialization, orchestration |
| **4 — Self-Evolving Ecosystem** | autonomous discovery, learning, reproduction, adaptation | meta-learning, self-modification, governance |

**Cross-cutting:** a built-in skill library + self-authored skills that refine over time · crons
(human-assigned and self-learned) · integrations via **MCP** + arbitrary REST/OpenAPI tools ·
migration from other agents (Hermes/OpenClaw) bringing config, memory and skills.

> Status: **MVP complete** (Tiers 1+2 + Fusion). Done: M0 foundations · M1 (Tier-1 +
> tools/skills/integrations/crons/migration) · M2 (LLM-Fusion engine + cost-aware router) ·
> M3 (Tier-2 autonomous loop: plan → execute → Manager review → verify-or-revert, with an
> experience buffer). Next: M4 (self-evolution: skill discovery, self-learned crons, memory merge).

## Why Chimera (the differentiation thesis)

Existing frameworks are each strong on one axis: Hermes/OpenClaw evolve skills but run a single model;
CrewAI/LangGraph orchestrate well but don't learn; TrustClaw/NemoClaw/ZeroClaw bring security/sandboxing
but don't evolve. **Chimera combines** (a) fusion-as-reasoning, (b) all four tiers in one progression,
(c) a multi-level self-evolution engine that explicitly attacks continuous-evolution degradation, and
(d) a governance kernel that *also* self-improves.

## Quickstart

Requires Python **3.11+** (3.12+ recommended) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env   # fill in at least one provider key (OpenRouter recommended)
uv run chimera doctor  # check your environment
uv run chimera version
```

### Commands

```bash
chimera doctor                       # environment + key check
chimera models                       # default model + fusion panel
chimera tools                        # list native tools (files, shell, http, ...)
chimera skills                       # list built-in skills
chimera run "PROMPT"                 # single-shot Tier-1 completion (needs a key)
chimera fuse "PROMPT" --show-panel   # LLM-Fusion: panel -> judge -> synthesizer
chimera agent "TASK" --fuse          # ReAct agent loop; route deep turns through fusion
chimera solve "TASK" --verify "pytest -q"   # Tier-2: plan -> execute -> verify-or-revert
chimera cron add NAME "0 9 * * *" "run report"   # schedule a job
chimera cron list                    # list scheduled jobs
chimera migrate hermes ~/.hermes     # preview importing config + skills (dry-run)
chimera migrate hermes ~/.hermes --apply
```

Add integrations from code: `chimera.integrations.OpenAPIConnector` turns an OpenAPI
spec into tools, and `connect_stdio(...)` (with the `mcp` extra) wraps an MCP server.

## Architecture (high level)

```
core/         agent loop (ReAct), planner, state machine, verify-or-revert
fusion/       panel -> judge -> synthesizer + cost-aware router
memory/       working / episodic / persona / graph + Memory Manager
skills/       built-in library + auto-created skills (test -> keep/discard)
tools/        native tools
integrations/ MCP client + OpenAPI->tool importer
scheduler/    crons (assigned + self-learned) + SOP engine
migration/    import from Hermes/OpenClaw (config, memory-merge, skills)
evolution/    multi-level self-evolution + experience buffer
governance/   trust kernel (allow/warn/block/review), static validator, audit, rollback
orchestration/ multi-agent roles, supervisor/swarm, MOC comms
providers/    LLM adapters (LiteLLM / OpenRouter)
sandbox/      execution backends (docker / local / remote)
eval/         benchmarks incl. continuous-evolution, LLM-as-judge
cli/ tui/     interfaces (CLI-first)
```

## License

[Apache-2.0](LICENSE).
