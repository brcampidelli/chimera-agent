# Vision

## The thesis

Today's best agents are each strong on a single axis and weak on the others:

- **Hermes / OpenClaw** evolve skills and curate memory — but run a *single* model and
  degrade on long, continuous work.
- **CrewAI / LangGraph** orchestrate teams and durable graphs — but they don't *learn*.
- **TrustClaw / NemoClaw / ZeroClaw** bring security and sandboxing — but they don't *evolve*.

The frontier paper *"Agentic Software"* names the open problem precisely: agents drop from
**>80% on isolated tasks to ~38% on continuous evolution** — the limits are long-horizon
context management and error propagation.

**Chimera is the combination none of them attempt:**

> **(a) Fusion-as-reasoning + (b) all four capability tiers in one progression +
> (c) a multi-level self-evolution engine that explicitly attacks continuous-evolution
> degradation + (d) a governance kernel that also self-improves.**

## Why fusion

Inspired by OpenRouter Fusion: asking several models the same question, having a judge
arbitrate their disagreement, and a synthesizer write the grounded final answer beats any
single frontier model on deep work. Crucially, the lift comes from the **synthesis process
itself** — so the judge and synthesizer matter as much as the panel. Chimera makes this the
reasoning core, and routes to it *selectively* (it costs 2–3× latency, so tool turns stay
single-model and only deep/high-stakes turns fuse).

## How we attack continuous-evolution degradation

1. **Externalize state** to the workspace/git, not the LLM context.
2. **Ownership-scoped context** (the Spine) instead of free-form repo search.
3. **Generate-vs-verify** supervision (Worker → Manager) to cut error propagation.
4. **Verify-or-revert** — keep a change only when executable evidence supports it.
5. **An experience buffer** — failures become negative examples.
6. A **continuous-evolution benchmark** that measures degradation from day one.

## The four tiers

1. **Augmented Tools** — autocomplete, point fixes, script generation (RAG).
2. **Single-Task Autonomous** — end-to-end features, debugging (plan + tools + self-correction).
3. **Multi-Agent Teams** — coordinated crews, shared memory, role specialization.
4. **Self-Evolving Ecosystem** — discovery, learning, reproduction, adaptation
   (meta-learning, gated self-modification, ecosystem governance).

## Principles

- **Working > perfect**, **simple > clever**, **search before create**.
- **Self-modification is gated** — a structured, statically-validated edit surface, never
  arbitrary code.
- **Never hard-block a benign action** in the governance kernel.
- **Human-in-the-loop for the destructive** — and for the automations the agent proposes.
- **Provider-agnostic and open** — Apache-2.0, MCP-native, no lock-in.

## Roadmap beyond v0

- Validation against real models at scale; expand the continuous-evolution suite into a
  stateful chain against an evolving repo.
- Optional **LangGraph** durability backend for long-running crews.
- Vector memory + a richer Memory Manager.
- Opt-in model-level evolution (LoRA/DPO) from collected trajectories — training stays
  external and explicit.
