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

### The taint layer (prompt-injection containment)

Layered on top of the kernel — heuristic, honest, and never a hard boundary (the sandbox is):

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — a per-run capability ledger.
  A fetch taints its content; a write/exec that consumes tainted content **escalates to review**
  (`assess_action`). Untrusted fetched content is returned **data-fenced** and with chat-template
  control tokens stripped (`sanitize.py`), and durable artifacts from a tainted run keep a
  `tainted` provenance so poison can't launder itself into a "clean" memory/skill.
- `AggregateMonitor` (`aggregate_monitor.py`) — a monitor one level up: given each sub-agent's
  capability events, it catches **split flows** a per-agent monitor can't see (agent A fetches
  untrusted content, agent B execs or **exfiltrates** it).
- `check_drift` (`drift.py`) — a `Spec` of executable requirements (`defines`/`contains`/`absent`/
  `command`) that doubles as the `solve --verify` ground truth and the project orchestrator's
  authority on "done" (below). Negative checks fail closed on files they can't scan.
- `QuarantineTool` + adaptive allowlist (`quarantine.py`, `allowlist.py`) — a dual-LLM/CaMeL
  quarantined reader and a taint-adaptive tool allowlist that narrows once a run is tainted.

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

## Cost economics & the delegation hierarchy

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

Delegation only pays when it's cheaper than doing the work inline, and the claim is **measured, not
asserted**:

- `HierarchicalOrchestrator` — decompose → dispatch budgeted workers → verify each result →
  synthesize. Read-shaped fan-out delegates; a trivially small subtask is answered inline by the
  trusted top model.
- `CascadeBackend` — weak → gate → mid → gate → fusion, climbing only when a tier's answer fails a
  cheap acceptance gate. The **route log** records every hop, so the cost is the **sum over hops
  tried**, not just the accepted one — escalations are paid for.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — a hard token ceiling enforced at the backend,
  per worker.
- `EnvelopeVerifier` — schema → acceptance criteria → probabilistic **spot check** (grade a summary's
  faithfulness against the raw artifact); a re-ask triggered by a spot failure is re-audited.
- **Delegation receipts** (`receipts.py`) — every delegation logs its measured tokens/cost **and the
  inline counterfactual in the same row**, priced at each model's own rate (unknown model → `None`,
  never fabricated). The orchestrator's own decompose/synth overhead is metered too, so
  `summarize_delegations` (`chimera delegations`) reports an **auditable** net saving, and
  `cascade-bench` reports the cost **tail** (p50/p95/p99), not just the mean.

## The self-evolution flywheel

`chimera/evolution/`

The "training" that never touches weights — fitness-signaled, gradient-free, and reversible:

- `EvolutionContext` — the shared assembly (experience, trajectories, memory, auto-evolver, skill
  cards, playbook) that makes learning a property of the agent *stack*, not just the `solve` command.
- Skill cards + **GEPA** refinement, ACE **playbook**, and a `SkillLifecyclePolicy` that promotes/
  demotes a skill by its **measured** use/success stats (a new skill is born `provisional`).
- The **diff-gate** — a "hollow success" (verifier passed but the workspace diff is empty) does not
  mint a skill or memory; the flywheel only learns from work that actually happened.
- The **transfer-gate** (`eval/transfer.py`) — a tuned artifact is promoted only if it also holds on
  a holdout, guarding against negative transfer. `maturity.Scorecard.weakest()` is the objective:
  the loop targets the weakest capability. Regressions auto-roll-back only on a **statistically
  significant** drop (a CI, never a single point).

Every flip of a default is gated behind a **pre-registered** paired A/B (`bench/`), published whether
it wins or loses — no re-rolling for significance.

## Project autonomy (start-to-finish)

`chimera/orchestration/project.py`

`ProjectOrchestrator` runs a whole project against a `Spec`: task-graph (a Kanban DAG with
`depends_on`) → each ready card solved (with the evolution context above) → **accepted against the
Spec** via `check_drift` (the only authority on "done") → unmet requirements generate the next cards,
looping until the Spec is aligned or a budget / max-iterations / human checkpoint stops it. Risky
steps (`risk: high` — deploy / migration / delete) **pause for human approval**; the run is durable
and resumable.

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
