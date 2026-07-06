# Chimera — Architecture

This document maps the codebase to the design and to the research it builds on. For the
"why", see [VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## The reasoning core: LLM-Fusion

`chimera/fusion/`

The fusion engine runs a task through a **panel** of models, has a **judge** produce a
structured analysis (consensus / contradictions / partial coverage / unique insights /
blind spots), then a **synthesizer** writes the final answer grounded in that analysis
(`FusionEngine`). It implements the `SupportsComplete` protocol, so it is a drop-in
reasoning backend anywhere a model is expected — including inside the agent loop.

A **cost-aware router** (`RoutedBackend` + `RoutingPolicy`) keeps fusion selective: tool-
calling turns go to a single model (fusion does not tool-call), and only deep / high-stakes
reasoning turns are fused. Inspired by OpenRouter Fusion (the lift comes from the *synthesis*
step, not only model diversity) and AURORA-AI (adaptive budget across heterogeneous models).

## The agent loop & Tier-2 autonomy

`chimera/core/`

- `Agent` — a minimal ReAct / tool-calling loop with an **explicit transcript** (state lives
  outside the model). Depends only on `SupportsComplete` + a `ToolRegistry`.
- `AutonomousAgent` — Tier-2: assemble ownership-scoped **Spine** context → **plan** →
  snapshot → execute → **Manager review** (generate-vs-verify) → **verify-or-revert** → retry
  with feedback, recording each attempt in the experience buffer.
- `WorkspaceGuard` — text-file snapshot/restore, the mechanism behind verify-or-revert.
- `CommandVerifier` — "executable evidence" (exit 0 == success).

### Attacking continuous-evolution degradation

The open problem (per *Agentic Software*, `2606.05608`): perf falls from >80% on isolated
tasks to ~38% on continuous evolution — long-horizon context + error propagation. Chimera's
countermeasures, each grounded in the literature:

| Countermeasure | Where | Basis |
|---|---|---|
| Externalize state (transcript/workspace, not LLM context) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Ownership-scoped context (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Generate-vs-verify supervision | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verify-or-revert | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Experience buffer (failures as negatives) | `evolution/experience.py` | HORIZON `2606.28279` |
| Message consolidation in teams | `orchestration/comms.py` | MOC `2606.02359` |
| Continuous-evolution benchmark | `eval/continuous.py` | EvoClaw problem statement |

## Memory & self-evolution

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — hierarchical items (working / episodic / semantic / persona) with
  `ADD / UPDATE / DELETE / NOOP` (`remember`) and `merge` dedup (Memory-R1, `2606.14502`).
- **Skill evolver** — `SkillEvolver` proposes a reusable `LearnedSkill` from a success, tests
  it, and keeps it only if it passes (propose → test → keep/discard). Learned skills are
  **prompt templates, not executable code** — safe to author autonomously before code-level
  self-modification. Refinement improves a template from its failures (VIBEMed `2606.15504`).
- **Self-learned crons** — `CronLearner` detects recurring tasks and proposes crons
  (`created_by=agent`, **disabled** pending human approval).
- **Continuous-evolution benchmark** — runs a chain of tasks through a solver and reports
  degradation (overall pass rate, first-half vs second-half, longest streak).

## Governance & safety

`chimera/governance/`

A self-improving trust kernel (AgentTrust v2, `2606.08539`):

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. Lexical `RuleSet`
  handles fixed-signature threats deterministically; an optional **semantic judge** handles
  intent; distilled rules make it cheaper over time. Invariant: **never hard-block a benign
  action**.
- `SkillValidator` / `ScheduleValidator` — the **constrained, statically-checkable edit
  surface** for self-modification (AutoMegaKernel `2606.09682`): unsafe proposals are rejected
  before they ever run.
- `AuditLog` — append-only JSONL of decisions and evolution changes.
- `GovernedTool` / `govern_registry` — wrap any tool so its execution is gated; composes with
  the existing agent loop unchanged (`chimera ... --guard`).

## Multi-agent teams (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — role specialization (CrewAI-style).
- `SequentialCrew` — roles in order, each sees the **consolidated** prior outputs and can
  write to shared memory.
- `SupervisorCrew` — workers address the task in parallel, outputs are consolidated, and a
  supervisor synthesizes (CAPRA-style `parallel_review`, `2606.18976`).
- `consolidate` — MOC message merging keeps team context lean (`2606.02359`).

## Self-evolving ecosystem (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — designs/builds/evaluates specialized agents (agents building agents). Two
  safeguards from the Meta-Agent Challenge (`2606.04455`): **tool isolation** (a designed
  agent's tools are filtered to an allowed list) and **hidden-test separation** (visible pass
  + hidden fail ⇒ reward-hacking suspected, not credited as success).
- `ChangeQueue` — governs change *tempo* (FIFO merge queue + batch caps), not headcount
  ("Govern the Repository", `2606.28235`).
- `TrajectoryCollector` — records (prompt, response, outcome) and exports **SFT / DPO**
  datasets. Actual fine-tuning is **opt-in and external** — Chimera collects, it doesn't train.

## Cross-cutting

- **Providers** (`providers/`) — one provider-agnostic gateway over LiteLLM; keys can live in
  `.env` and are exported to the environment so LiteLLM sees them.
- **Tools** (`tools/`) — native primitives; tool metadata are instance attributes so
  dynamically generated tools (OpenAPI/MCP) work.
- **Integrations** (`integrations/`) — MCP client (optional `mcp` extra) + OpenAPI→tool
  importer + connector registry.
- **Scheduler** (`scheduler/`) — crons + event SOPs; time is injected for deterministic tests.
- **Migration** (`migration/`) — import config + skills + **merge** long-term memory from
  Hermes / OpenClaw, deduped and non-destructive.

## Testing philosophy

Every subsystem is unit-tested with **fake backends** — deterministic, no network, no keys.
Commands that actually call an LLM are smoke-tested for their no-key failure path. The quality
gate (`ruff` + `mypy --strict` + `pytest`) runs in CI on Python 3.11 and 3.12.
