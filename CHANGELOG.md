# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **`IsolatedCrew`** — composes the three subagent primitives into one: tool-using workers
  each tackle the SAME task in their OWN git worktree, in parallel. Non-conflicting edits merge
  back; a file two workers both changed is reported as a conflict, not clobbered; a crashing
  worker fails only its own unit. `IsolatedWorker(role, tools_factory, backend?)` — the tools
  factory roots each worker's registry at its isolated checkout. This is the division-of-labour
  counterpart to `solve-batch` (which runs N *separate* tasks): here N specialised workers split
  ONE task with real filesystem isolation.
- **Generic subagents** — generalises the Context Explorer pattern two ways. **`SubAgentTool`**
  (`solve --subagents`) gives the main agent a `spawn_subagent(task, tools)` tool: it runs a
  fresh Agent in its own context with only an allowed subset of tools and returns ONLY the final
  result — so the main agent can fan work out or offload context-heavy subtasks. Two guardrails:
  no recursion (a subagent is never granted the spawn tool) and it can't exceed the configured
  allowlist. **Tool-using `RoleAgent`** — a crew role can now be given a tool registry, turning it
  from a single-shot persona into a real worker that runs its own loop (search/read/edit/run) and
  returns its answer; crews call `act()` either way, so talkers and doers mix transparently.
  Verified live: a subagent used grep/read to locate `MAX_RETRIES` and returned just the value.
- **Context Explorer** (`chimera explore`, `solve --explorer`) — a FastContext-style
  (arXiv 2606.14066) isolated repository-exploration subagent. It takes a natural-language
  query, runs its own bounded read-only search, and returns only a compact `file:line`
  evidence block — its search turns never touch the main agent's context (the token/degradation
  win of separating *exploration* from *solving*). Runs on any backend; a cheap model is ideal,
  since localization is a narrow task. Ships with new **`grep`** (regex over contents) and
  **`glob`** (path patterns) native tools. Verified live: located the fusion engine at
  `fusion/engine.py:1-165` with a cheap model in 6 turns. (The paper's *trained* 4B/30B explorer
  is a separate Tier-4 aspiration via the `evolve` pipeline; this is the untrained architecture.)
- **Parallel multi-agent isolation** (`chimera solve-batch`, `chimera.orchestration.run_isolated`):
  solve several tasks concurrently, each in its **own git worktree**, so parallel file edits
  never collide. On merge-back a file two tasks both changed is reported as a **conflict** and
  left for you to resolve rather than silently overwritten (mechanical "one file, one owner").
  A crashing/hanging worker becomes a failed result instead of taking down the batch. Plus
  `run_in_processes` for fault/CPU isolation of self-contained units across a process (RPC)
  boundary. Closes the distributed-isolation gap for single-box production scale.
- **Skill nudges**: during `chat`, when the same kind of request recurs and no skill covers
  it, Chimera suggests saving it as a reusable skill ("🛠️ done this 3× — save as a skill?").
  The skill analogue of memory nudges: pure/deterministic (reuses token-Jaccard clustering),
  shown once each, and suppressed when an existing learned skill already covers the task. It
  only surfaces — the autonomous `AutoSkillEvolver` is still what actually writes skills.
- **Budgeted auto-consolidation** (`CHIMERA_AUTO_CONSOLIDATE=1`, `CHIMERA_MEMORY_BUDGET=N`):
  on `chat` exit, if memory has grown past the budget, near-duplicate facts are consolidated
  with the model. Skipped entirely while memory is small (no wasted calls); best-effort, never
  blocks exit. `MemoryManager.autoconsolidate()` is the reusable primitive.
- **LLM memory consolidation** (`chimera memory consolidate`): clusters near-duplicate facts
  by token-Jaccard similarity and merges each cluster into one model-summarised fact, cutting
  memory bloat while preserving specifics. Complements value-based `prune` (which drops) — this
  *merges*. Clustering is pure/deterministic; the summariser is injected, so the logic is
  tested without a model. An opt-in write (never runs automatically).
- **Memory nudges**: during `chat`, when you state a first-person preference ("I prefer async",
  "I always use ruff") that isn't in memory yet, Chimera surfaces a gentle "💡 remember this?"
  with the exact `memory add --persona` command. Deterministic, deduped, shown once per session;
  a token-overlap check means it won't re-nudge something already stored. Low-friction path to
  building the cross-session profile.
- **Cross-session user profile**: persona memories are consolidated into a profile preamble
  (`chimera memory profile`) that's applied on *every* turn of `chat` / `tui` / `serve` — so
  the agent remembers the user's preferences across conversations without them re-stating it.
  Record them with `memory add --persona`. Closes the cross-session personalization gap.
- **SQLite + FTS5 memory backend** (`CHIMERA_MEMORY_BACKEND=sqlite`): an optional store with
  a full-text index, so recall is phrase/substring-aware and stays fast as memory grows —
  addressing the top memory gap vs. Hermes (keyword-only JSON). The `MemoryManager` prefers
  a backend's `search` when present; JSON stays the zero-dependency default. Degrades to a
  `LIKE` search if a Python build lacks FTS5.
- **Native Signal** (`chimera serve --signal`): two-way via a `signal-cli-rest-api` bridge
  you run (Docker) — poll `GET /v1/receive` + `POST /v2/send` over `httpx`, no Python
  dependency, same adapter shape as Telegram. Pure envelope parsing/filtering is fully
  tested. Config: `CHIMERA_SIGNAL_API_URL` + `CHIMERA_SIGNAL_NUMBER`. (The bridge is
  external, but the adapter is real and tested — not a stub.)
- **Stateful + productivity tools**: `code_interpreter` (a persistent in-process Python
  session — variables/imports carry across calls, `reset` to clear), `read_email` (IMAP,
  stdlib) and `calendar_events` (any iCalendar `.ics` feed, stdlib parser). `code_interpreter`
  is always on; the other two auto-register when their config is set.
- **WhatsApp (two-way)**: a `WhatsAppSender` (Cloud API over `httpx`) lets the agent send
  via `send_message` in any `serve` mode; and `chimera serve` now serves the inbound
  webhook — `GET /whatsapp` does the Meta subscription verification (echoing the challenge
  as plain text) and `POST /whatsapp` routes messages through the gateway and replies. Set
  `CHIMERA_WHATSAPP_ACCESS_TOKEN` + `_PHONE_NUMBER_ID` + `_VERIFY_TOKEN` and point the Meta
  webhook at `https://<host>/whatsapp`. Verification + routing are pure and tested; only the
  public URL lives outside.
- **Webhook triggers — unattended operation.** The scheduler gained a `webhook` trigger
  (`chimera cron add <name> <hook> <task> --webhook`), and the gateway serves
  `POST /webhook/<hook>`: an inbound HTTP request fires every job registered for that hook,
  with the POST body handed to the task as context. Chimera can now run on a GitHub push, a
  Stripe event, or any external ping — no human in the loop. The routing lives in the pure,
  tested `handle()`; `_serve` wires it to the scheduler.
- **More reference tools**: `execute_code` (run Python through the sandbox — same isolation
  and governance as `run_shell`) and `arxiv_search` (public arXiv API, stdlib XML) are
  always on; `youtube_transcript` is opt-in (the `youtube` extra), degrading gracefully
  when the library or a transcript is unavailable.
- **Reference tool library** (batteries, key-gated like `web_search`): `generate_image`
  (OpenAI Images → saves a file), `text_to_speech` (ElevenLabs → saves an mp3), and
  `send_email` (SMTP, Python stdlib — no dependency). Each auto-registers when its
  credential is present, so the agent gains the capability the moment you add the key.
  Starts closing the "only 6 built-in tools" gap vs. Hermes.
- **Native Slack** (`chimera serve --slack`): the third platform on the same adapter
  pattern — receives via Socket Mode (`slack-sdk`, the `messaging` extra) and sends via the
  Web API (`chat.postMessage`, plain `httpx`). Pure event-filtering (`_message_from_event`)
  is fully tested. Tokens via `CHIMERA_SLACK_BOT_TOKEN` + `CHIMERA_SLACK_APP_TOKEN`. Three
  platforms now share one `_serve_platform` — the adapter pattern is proven to generalise.
- **Native Telegram** (`chimera serve --telegram`): a second platform on the same adapter
  pattern (Adapter + MessageSender + pure message-filtering), proving it generalises. Uses
  the Telegram Bot API over plain HTTP — **no extra dependency** (just the core `httpx`).
  Token via `CHIMERA_TELEGRAM_BOT_TOKEN`. The Discord/Telegram chunking helper was hoisted
  to `chunk_text`, and the CLI's platform serving is now a generic `_serve_platform`.
- **Native Discord** (`chimera serve --discord`): Chimera runs as a Discord bot — each
  channel is its own session, it replies in-channel, and it ignores its own and other
  bots' messages (with an optional user allowlist). Plus a platform-agnostic messaging
  layer (`SenderRegistry` + a `send_message` tool) so the agent can also *send* messages
  on connected platforms. `discord.py` is the opt-in `messaging` extra; the bot token is
  read from `CHIMERA_DISCORD_BOT_TOKEN` (never hard-coded). This closes the biggest
  integrations gap vs. Hermes and establishes the adapter pattern for Telegram/Slack next.
- **Entity-aware recall**: `ChatSession` now also pulls facts linked (via the memory
  graph) to entities named in a message, not only keyword hits — so "tell me about Stripe"
  recalls "Stripe is our payment provider" even without a shared keyword. Wired into
  `chat`, `tui`, and the messaging gateway.
- **Resilient REST tools**: OpenAPI-generated tools now retry on 429 / 5xx / transport
  errors with exponential backoff, honouring a `Retry-After` header — so an agent hitting
  a rate-limited public API recovers instead of failing the turn.

### Fixed
- **`bench --fuse` now measures fusion, not the router.** It used a cost-aware
  `RoutedBackend`, whose length/keyword gate declines to fuse short prompts — so on the
  hard chain it silently collapsed back to single-model (degradation 1.0, same as no
  fusion). It now uses the `FusionEngine` directly, matching the flag's documented intent;
  `bench --hard --chain --fuse` holds 8/8 (degradation 0.0) where single collapses.

### Added
- **Hard benchmark suites** (`chimera bench --hard`): 12 reasoning-trap tasks and an
  8-step **stateful** arithmetic chain where an error *propagates*. Unlike the trivial
  demo sets (which ceiling at 100%), these expose EvoClaw degradation — measured live, a
  single model breaks mid-chain and collapses 100% → 0% in the second half (degradation
  1.0), while fusion holds 8/8 (degradation 0.0). A deterministic `OracleSolver` test
  encodes the propagation collapse permanently.
- **`chimera evolve tune`** (OpenJarvis): self-optimize the agent spec via meta-search —
  each round a model proposes a coordinated edit, scored on the daily scenarios and kept
  only on non-regression. `scenario_scorer` turns the scenario suite into a reusable
  scorer for `search_spec`.

### Changed
- **The fusion router now fuses short but error-sensitive turns.** Its gate was length +
  reasoning-keywords only, so exact-answer tasks (arithmetic, counting, digit ops) fell
  through to a single model — precisely where a lone slip corrupts a long chain.
  `RoutingPolicy` gained precision-keyword + arithmetic-expression detection, with keyword
  sets in the project's main languages (en/pt/es/de/fr/zh/ja), on by default
  (`fuse_error_sensitive`, opt-out). Measured live through the *same* `RoutedBackend`: the
  hard chain collapses with it off (degradation 1.0) and holds with it on (0.0).
- **`solve`/`crew` can auto-fuse without `--fuse`.** New `CHIMERA_AUTO_FUSE` (default off,
  since fusion costs 2-3x) routes the worker through the cost-aware router in production,
  so deep/error-sensitive turns fuse while cheap/tool turns stay single-model. `--fuse`
  still additionally routes deep *planning* through fusion.
- **`RoutingPolicy.fuse_reason()`** reports *why* a turn does or doesn't fuse
  (length / keyword / precision / arithmetic / none) — for cost auditing and telemetry.
  Calibrated against a mixed session corpus: fusion fires on 0% of casual / coding /
  chit-chat turns and 100% of reasoning / exact / long turns, ~19% of a typical session;
  the arithmetic gate now also catches percentages (`15% of 80`).
- **Cascade rubric as a review criterion.** `solve --rubric` makes the Manager judge a
  result on the cascade rubric (instruction-following → factuality → rationality),
  approving on the importance-weighted overall and naming the weakest dimension on a
  revision. Default review is unchanged.
- **Collective skill + step attribution are now wired into the autonomous loop.** The
  auto-evolver proposes a recurring skill across the fusion panel and keeps the most
  transferable one when `solve --fuse` runs with a ≥2-model panel (falls back to
  single-model otherwise). And a failed `solve` attempt folds the SkillAdaptor
  step-level diagnosis — the first failed tool step — into the retry feedback, so one
  early error is pinpointed instead of diffusing across the next attempt.

### Added
- **Collective skill evolution** (OpenClaw-Skill, 2606.16774): `CollectiveSkillEvolver`
  proposes a candidate skill from each model of the fusion panel and keeps the one that
  **transfers best** across the panel, gated by the governance validator — cross-model
  agreement as the quality signal.
- **Step-level failure attribution** (SkillAdaptor, 2606.01311): `localize_fault` finds
  the first failed tool step in a transcript, `attribute` links it to the most-overlapping
  skill, and `qualify` accepts a revision only on non-regression — precise blame instead of
  diffusing a single early error across unrelated steps.
- **Cascade rubric evaluation** (DailyReport, 2606.12871): `evaluate_cascade` scores an
  answer across importance-weighted dimensions (instruction-following → factuality →
  rationality) as a cascade — a downstream dimension is scored only if the upstream clears
  its gate.
- **Self-optimizable agent spec + meta-search** (OpenJarvis, 2605.17172): `AgentSpec`
  bundles the agent's editable primitives into one optimizable unit; `search_spec` runs a
  propose → evaluate → keep-on-non-regression loop (`model_proposer` emits coordinated
  edits) — config-level self-improvement gated against drift.
- **Data-recipe curation** (Data Recipes for Agentic Models, 2606.24855): SFT curation
  gained two opt-in knobs — `evolve export --min-steps N` keeps only long-horizon traces
  (deeper tool-use is higher-value supervision) and `--diverse` caps examples to one per
  task (task-description diversity is the curation bottleneck). Trajectories now record
  their tool-calling step count. Defaults preserve current behaviour.
- **Memory admission gate** (MemGate, 2606.06054): recall now passes through a trust
  boundary — a recalled memory enters the prompt only if it is relevant to the query
  *and* free of override/injection markers (a memory-based jailbreak defense). On by
  default in `chat`/`tui`/`serve`. Verified live (an injected memory was blocked, the
  clean one admitted).
- **Multi-factor memory value + pruning** (2606.12945): `memory prune --max N` keeps the
  highest-value memories under a budget, scored by a weighted multi-factor model
  (recency, specificity, kind, curation, reliability) instead of a single cue — the
  interpretable, deterministic version of value-directed forgetting. Verified live.
- **Governance fidelity — precedent RAG + four-actor model**: the `TrustKernel` now
  carries a guarded `PrecedentStore` — a judge verdict becomes a usable precedent only
  after **two agreements** for the same action, after which a *similar* action is
  decided by recalling the precedent (token-overlap RAG) instead of re-invoking the
  expensive judge (AgentTrust v2's guarded precedent). And `FourActorGovernance` runs a
  change through **author → reviewer (advisory) → gatekeeper (authoritative hard gate) →
  auditor (audit log)**, separating advice from authority (Spec Growth Engine's 4-actor
  model). Closes the last two paper sub-mechanisms.
- **Prompt caching** (`CHIMERA_CACHE=on`, HORIZON): an exact-match completion cache
  returns a stored result for an identical tool-free `(model, messages, temperature,
  max_tokens)` request, skipping the API call — saving cost/latency on repeated
  reasoning turns (fusion panel/judge/synth, planner, reviewer, benchmark re-runs).
  Opt-in; tool turns always hit the model live. Verified live (the same prompt returned
  the same answer on the cached call, with no second API call).
- **Drift gate — spec↔code** (`chimera drift <spec.yaml>`, Spec Growth Engine): a spec
  is a small YAML of requirements (`defines` a symbol / `contains` a regex / `absent` a
  regex / `command` exits 0); the gate checks the workspace against it and **exits
  non-zero on drift**, so spec and code stay aligned or the change is rejected. Doubles
  as a verifier (`solve --verify "chimera drift spec.yaml"`). Example in
  `examples/spec.yaml`; verified live (aligned, then a stray TODO produced drift).
- **Graph memory layer** (`chimera memory graph`): extracts `(source, relation, target)`
  triples from long-term memory with a deterministic heuristic extractor, building an
  entity-relation graph so facts can be recalled by **entity** (`related_facts`) rather
  than only by keyword — the VIBEMed `graph` layer (alongside working/episodic/semantic/
  persona). `memory graph --entity X` shows one entity's relations. Verified live.
- **Git-worktree isolation** (`chimera solve --isolate`, HORIZON-style): when the
  workspace is a git repo, the run executes in a throwaway worktree on its own branch
  — the agent's edits never touch the main checkout until they're verified, then only
  the files it actually changed are copied back (on success) or discarded (on failure).
  A no-op outside a git repo. Verified live (built a file in isolation, copied back on
  success, worktree removed).
- **Loop Engineering — declarative workflows** (`chimera workflow <file>`): author an
  autonomous loop as YAML — an ordered list of steps that `use` the agent stack
  (`run` / `shell` / `solve` / `crew` / `lifecycle`), gate on the previous step
  (`when: prev_succeeded|prev_failed`), and loop (`repeat` up to N, `until: success`).
  Designed flows instead of ad-hoc prompts. The runner takes injected executors, so
  the control flow is fully unit-tested; the real executors dispatch to the stack.
  Example in `examples/workflow.yaml`; verified live (a solve+verify build step, then
  a report step gated on its success).
- **SDLC lifecycle crew** (`chimera lifecycle`): a pre-assembled **plan → build →
  test → review** pipeline. `plan` decomposes the task, `build` implements it, `test`
  runs the verifier as the **verify-or-revert** gate (revert + retry on failure), and a
  reviewer role critiques the verified result. Built on the Tier-2 `AutonomousAgent`,
  so the per-stage gate is the same executable ground truth used everywhere. Verified
  live (built `solution.py`, verified `add(2,3)==5`, then reviewed).
- **Docker execution sandbox** (`CHIMERA_SANDBOX=docker`): the shell tool can now run
  each command inside an ephemeral, network-isolated container
  ([docker/Dockerfile.sandbox](docker/Dockerfile.sandbox)) — workspace bind-mounted,
  root fs discarded (`--rm`), memory capped, network off by default — instead of
  directly on the host. A `Sandbox` seam with `LocalSandbox`/`DockerSandbox`; the
  default stays `local`. Degrades gracefully to local when Docker is unavailable
  (verified live: the fallback ran the command and logged a warning).
- **Kanban ↔ cron-learner — recurring tasks become cards** (`chimera kanban learn`):
  reuses the cron-learner's recurrence detector over the experience buffer to create
  backlog cards for tasks the agent repeats (per-card confirmation, `--yes`, deduped
  against the board). Schedule it to auto-fill the backlog; then `kanban run` dispatches
  the cards. Verified live (two recurring tasks queued, the one-off excluded).
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
