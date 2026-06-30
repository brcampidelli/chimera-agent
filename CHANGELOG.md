# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Docker execution sandbox** (`CHIMERA_SANDBOX=docker`): the shell tool can now run
  each command inside an ephemeral, network-isolated container
  ([docker/Dockerfile.sandbox](docker/Dockerfile.sandbox)) — workspace bind-mounted,
  root fs discarded (`--rm`), memory capped, network off by default — instead of
  directly on the host. A `Sandbox` seam with `LocalSandbox`/`DockerSandbox`; the
  default stays `local`. Degrades gracefully to local when Docker is unavailable
  (verified live: the fallback ran the command and logged a warning).
- **Kanban board + worker lanes** (`chimera kanban`): a JSON-backed task board
  (backlog → doing → review → done) where each card names a worker *lane* that
  dispatches it to the agent stack — `solve` (Tier-2 autonomous, verify-or-revert) or
  `crew` (Tier-3 role pipeline). `kanban add/board/move/rm` manage cards; `kanban run`
  pulls backlog cards through their lanes (success → done, failure → review). The
  operational-orchestration surface — the loop the agent already runs, made visible
  and queued. Verified live (a solve card and a crew card dispatched to done).
- **Behavioural learning loop (1/3) — experience → planner**: `solve` now recalls the
  most relevant prior experiences (`ExperienceBuffer.relevant`, by task-token overlap,
  failures favoured) and folds them as a "lessons" block into the planner and worker
  context, so the agent avoids repeating past failure modes across runs/sessions.
  Advisory only — verify-or-revert still decides success, so a misleading lesson can
  never corrupt the workspace.
- **Behavioural learning loop (2/3) — auto-write memory on success**: a verified-
  successful `solve` now curates one deduped long-term memory fact (keyed per task,
  so re-solving UPDATEs the entry rather than bloating memory). Only verified
  successes are written — the verify-or-revert gate keeps failed/unverified work out
  of memory; `--no-remember` opts out. Later `chat`/`crew` recall then surfaces it.
- **Behavioural learning loop (3/3) — auto-evolve skills on recurrence**: when a task
  pattern recurs (≥ 2 prior verified successes), a verified-successful `solve` proposes
  a reusable `LearnedSkill` and keeps it only if it clears two gates — the
  `SkillValidator` (governance) and an executable smoke test (the skill must run and
  produce output). Stored deduped by name; `--no-evolve-skills` opts out. Verified
  live: solving the same task three times produced a validated, stored skill on the
  third run. **This closes the cross-task behavioural learning loop** (within-task
  verify-or-revert was already closed).

### Changed
- **Self-learned crons — now interactive (enabled with confirmation)**: `chimera cron
  learn` turns recurring tasks (from the experience buffer) into cron jobs through an
  explicit per-proposal confirmation — the human-in-the-loop approval that keeps
  automation creation under control. Confirmed proposals are validated
  (`ScheduleValidator`) and created **enabled**; `--yes` confirms all, `--schedule`
  overrides the suggested time. Previously it only registered disabled proposals
  awaiting a separate `cron enable`.

### Fixed
- **MCP stdio client teardown**: the live session now opens and closes the stdio
  client's `AsyncExitStack` in a single background task, fixing an anyio
  "exit cancel scope in a different task" crash on `close()` (surfaced by the new
  live MCP test). `list_tools`/`call_tool` were already working; only teardown broke.

### Added
- **Live validation — OpenAPI importer, MCP client & TUI**: opt-in integration
  tests now (1) import a real public OpenAPI spec (httpbin, 73 operations), pour
  the generated tools into a `ToolRegistry`, and call one live (real HTTP 200);
  (2) spawn a real MCP server over stdio (a FastMCP server) and drive it through
  Chimera's client — real `initialize`/`tools/list`/`tools/call` handshake, then
  register + call `add`/`echo` (also verified live against the third-party
  `@modelcontextprotocol/server-everything`). A headless Textual driver smoke
  drives the TUI through the real event loop (type → submit → worker reply,
  `/model` switch, `/exit`). Closes the remaining "unit-only" gaps.
- **AI providers — credential pools / key rotation**: `CHIMERA_<PROVIDER>_KEYS`
  (comma-separated) gives a provider a pool of keys, rotated round-robin across
  calls (spreading load / rate limits) with failover to the next key within a
  single call. Thread-safe (the fusion panel calls concurrently); a pool-only
  provider counts as configured. Verified live (an invalid key failed over to a
  working one) and confirmed the fusion path is unaffected.
- **AI providers — self-hosted, fallback chain & live model switch**:
  `CHIMERA_API_BASE` sends requests to a custom OpenAI-compatible endpoint
  (Ollama, vLLM, …); `CHIMERA_FALLBACK_MODELS` (comma-separated) fails over to the
  next model when the primary errors; and `/model <slug>` switches the model
  mid-session in `chat`/`tui`. OAuth/subscription provider logins remain a
  documented preset (not wired). Verified live (broken primary fell back; `/model`
  switched models).

- **Vision / image paste**: `Message` now carries images (local paths or URLs,
  base64 data-URL encoded) in the OpenAI/LiteLLM multimodal format; `chimera run
  --image <path|url>` (repeatable) sends them to a vision model. Verified live
  (gemini-2.5-flash read a generated image's colour).
- **Deliverable Mode** (`chimera deliver`): produce a polished, self-contained
  artifact (report/plan/spec, md/txt/html) and write it to a file; `--fuse` for
  higher quality. Verified live.
- **Pet / companion** (`chimera pet`): a persistent virtual companion with stats
  that decay over time (feed/play/rest); deterministic logic, no key needed.
- **Optional-features presets** (`chimera features`): pre-set credential slots
  (Tavily/Brave/Serp, X, Stability, ElevenLabs, Spotify) + a live readiness
  checklist showing which capabilities are on and what each needs (a key or a
  dependency). Reference `web_search` tool (Tavily) auto-registers when
  `TAVILY_API_KEY` is set — the template for the rest (others also plug in via
  MCP / OpenAPI->tool).

- **M8 — Interfaces (in progress)**: a shared conversational `ChatSession` core
  (multi-turn, memory-aware) and an interactive `chimera chat` REPL — the
  foundation the TUI and messaging gateway will reuse.
- **M8 — EvoClaw stress test**: `chimera/eval/evoclaw.py` runs the same stateful
  chain in two regimes — *naive* (errors propagate) vs *guarded* (externalized
  state + verify-or-revert + retry) — and reports the degradation gap. New
  `chimera evoclaw` CLI runs the A/B against a real model. Tests prove the guard
  resists the propagation that sinks the naive regime.
- **M8 — Right-hand scenario suite**: `chimera/eval/scenarios.py` + `chimera
  scenarios` run an everyday-assistant task set (date/unit conversions, sentiment,
  email extraction, action items, summarization) with deterministic checks. 7/7
  pass live against a real model.
- **M8 — Full-screen TUI** (`chimera tui`, Textual): a scrolling chat log + input
  + status bar over the same `ChatSession`. Blocking model calls run in a thread
  worker so the UI stays responsive. Adds `textual` as a dependency.
- **M8 — Messaging gateway + HTTP server** (`chimera serve`): a `MessageGateway`
  routes each chat to its own `ChatSession` (per-conversation context, shared
  long-term memory); a stdlib HTTP transport exposes `POST /chat` and `GET
  /health`, and a `LocalAdapter` covers in-process use. Discord/Telegram adapters
  plug into the same `on_message` seam next. Verified live (per-chat memory).
- **M8 — Opt-in model evolution** (`chimera evolve`): `solve --collect` logs
  trajectories; `evolve` curates them into SFT/DPO datasets (reward gating, dedup,
  preference margins), reports training readiness, and emits a runnable LoRA recipe
  (train.py + README + requirements). Training stays external/opt-in — never
  automatic. Optional `train` extra for the heavy libs.

## [0.1.0] - 2026-06-30

First tagged release. The initial build plan (M0–M7) is complete, then hardened
against real provider models. Highlights, by milestone:

### Added
- **M0 — Foundations**: package scaffold, provider-agnostic LLM gateway (LiteLLM),
  config (pydantic-settings), telemetry, and the `chimera` CLI.
- **M1 — Tier 1 & cross-cutting**: native tools (files/shell/http), the ReAct agent
  loop, Tier-1 skills (complete/fix/generate) + skill-context retrieval, MCP client +
  OpenAPI→tool importer, scheduler (crons + event SOPs), migration from Hermes/OpenClaw.
- **M2 — LLM-Fusion engine**: panel → judge → synthesizer, plus a cost-aware router
  (tool turns single-model, deep reasoning fused).
- **M3 — Tier 2 autonomous**: plan → execute → Manager review → verify-or-revert
  (workspace snapshot/restore + command verifier) + experience buffer. *MVP complete.*
- **M4 — Self-evolution v1**: Memory Manager (ADD/UPDATE/DELETE/NOOP dedup), memory-merge
  in migration, learned-skill evolver (propose→test→keep/discard), self-learned crons,
  continuous-evolution benchmark.
- **M5 — Governance kernel**: allow/warn/block/review trust layer, lexical rule set +
  optional semantic judge, static validators (skill/schedule), audit log, governed tools.
- **M6 — Tier 3 multi-agent**: roles, sequential & supervisor crews, MOC message
  consolidation, shared memory, parallel review.
- **M7 — Tier 4 ecosystem**: meta-agent (agents building agents) with tool isolation and
  hidden-test reward-hack detection, change-tempo governance, trajectory collection
  (SFT/DPO export) seeding opt-in model evolution.

### Hardened (post-M7, validated against real OpenRouter models)
- **Tier-2 correctness**: the executable verifier is now authoritative — a strict
  Manager can no longer veto and revert work that already passed verification
  (a data-loss bug found only under live testing). Manager verdict parsing also
  tolerates markdown/preamble.
- **`solve --fuse`** now routes the *plan* through the fusion engine (deep,
  tool-free reasoning); previously the flag was effectively a no-op.
- **Stateful chained benchmark** (`bench --chain`) measuring error propagation.
- **Windows**: CLI forces UTF-8 output so model text never crashes a cp1252 console.
- **Hermetic tests** (no accidental network) + an opt-in live integration smoke test.

### Quality
- 166 tests · `mypy --strict` clean · `ruff` clean · CI across Python 3.11/3.12 +
  opt-in live integration job. Usage guide in `docs/usage.md`.
