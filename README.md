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

> **Status:** early alpha. All 8 milestones of the build plan (M0–M7) are implemented:
> Tiers 1–4 + the Fusion engine + self-evolution + a governance kernel.
> 158 tests · `mypy --strict` clean · `ruff` clean.

---

## Why Chimera

Existing frameworks are each strong on one axis: Hermes/OpenClaw evolve skills but run a
single model; CrewAI/LangGraph orchestrate well but don't learn; TrustClaw/NemoClaw/ZeroClaw
bring security/sandboxing but don't evolve. **Chimera combines all four:**

- 🧬 **Fusion-as-reasoning** — the panel→judge→synthesizer engine is the reasoning core, not an add-on. The lift comes from the *synthesis* step itself, not only model diversity.
- 🪜 **Four capability tiers in one progression** — augmented tools → single-task autonomous → multi-agent teams → self-evolving ecosystem.
- ♻️ **Multi-level self-evolution** that explicitly attacks continuous-evolution degradation (externalized state, drift-resistant context, verify-or-revert, experience buffer).
- 🛡️ **A governance kernel that also self-improves** — allow/warn/block/review, with a statically-validated self-modification surface.

## Features

- **LLM-Fusion engine** — provider-agnostic panel of frontier + open models, a judge that surfaces consensus/contradictions/blind-spots, and a synthesizer; a **cost-aware router** fuses only when it pays (tool turns stay single-model).
- **Tier-2 autonomy** — plan → execute → Manager review → **verify-or-revert** (workspace snapshot/restore + a command verifier), with a git-style experience buffer.
- **Self-evolution** — a Memory Manager (ADD/UPDATE/DELETE/NOOP dedup), a skill evolver that *writes and tests its own skills* (propose → test → keep/discard), self-learned crons, and a **continuous-evolution benchmark** that measures degradation.
- **Multi-agent teams** — role specialization, sequential & supervisor crews, MOC message consolidation, shared memory, parallel review.
- **Governance & safety** — a self-improving trust kernel, a static validator for the self-modification edit surface, an append-only audit log, and governed tools.
- **Integrations** — first-class **MCP** client + an **OpenAPI/REST → tool** importer, so you can add any platform or API.
- **Crons & proactivity** — human-assigned and self-learned scheduled jobs.
- **Migration** — import config, skills and **long-term memory** from Hermes Agent / OpenClaw (memory is *merged*, never overwritten).
- **CLI-first** — everything works from the terminal; provider-agnostic via LiteLLM/OpenRouter.

## Quickstart

Requires Python **3.11+** (3.12+ recommended) and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # set at least one provider key (OpenRouter recommended)
uv run chimera doctor       # check your environment
```

## Commands

```bash
chimera doctor / models               # status & configuration
chimera run "PROMPT"                   # single-shot Tier-1 completion
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: panel -> judge -> synthesizer
chimera agent "TASK" --fuse --guard    # ReAct agent loop (governed tool calls)
chimera solve "TASK" --verify "pytest -q"   # Tier-2 autonomous: plan -> verify-or-revert
chimera crew "TASK" --mode supervisor  # Tier-3 multi-agent crew
chimera meta "an agent for X"          # Tier-4 meta-agent: design a specialized agent
chimera memory add "a durable fact"    # curated long-term memory (deduped)
chimera cron add NAME "0 9 * * *" "run report"   # schedule a job
chimera cron learn                     # propose crons from recurring tasks (disabled)
chimera bench                          # continuous-evolution benchmark
chimera guard "rm -rf /"               # preview a governance verdict
chimera migrate hermes ~/.hermes --apply   # import config + skills + merge memory
```

See the **[Usage Guide](docs/usage.md)** for install, configuration, and every command with copy-paste examples.

## Architecture

```
chimera/
  core/          agent loop (ReAct) + Tier-2 autonomy (plan, verify-or-revert, supervisor)
  fusion/        panel -> judge -> synthesizer + cost-aware router
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        built-in library + skill-context retrieval
  evolution/     learned-skill evolver, experience buffer
  governance/    trust kernel (allow/warn/block/review), static validator, audit, governed tools
  orchestration/ roles, sequential & supervisor crews, MOC comms
  ecosystem/     meta-agent, change-tempo governance, trajectory collection
  tools/         native tools (files, shell, http)
  integrations/  MCP client + OpenAPI->tool importer
  scheduler/     crons (assigned + self-learned) + SOP engine
  migration/     import from Hermes/OpenClaw (config, skills, memory-merge)
  providers/     LLM adapters (LiteLLM / OpenRouter)
  eval/          continuous-evolution benchmark, demo tasks
  cli/           the `chimera` command (CLI-first)
```

See [docs/architecture.md](docs/architecture.md) for the full design and the research it builds on.

## Roadmap

| Milestone | Status |
|---|---|
| M0 — Foundations (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + tools/skills/integrations/crons/migration | ✅ |
| M2 — LLM-Fusion engine + cost-aware router | ✅ |
| M3 — Tier 2 autonomous (verify-or-revert) | ✅ |
| M4 — Self-evolution (memory, skills, learned crons, benchmark) | ✅ |
| M5 — Governance kernel | ✅ |
| M6 — Tier 3 multi-agent teams | ✅ |
| M7 — Tier 4 self-evolving ecosystem | ✅ |

Next: validation against real models at scale, an expanded continuous-evolution suite, and an optional LangGraph durability backend.

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
