# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Diff-gate for evolution (M15-A1).** An evolution/training target is now certified by the run's
  *real working-tree diff*, not the model's claim of success (nanobot "Dream" discipline).
  `chimera/evolution/diff_gate.py` classifies two workspace snapshots into added/removed/modified
  (ignoring whitespace-only churn and touched-empty files) and emits a machine-derived audit
  summary. The autonomous loop records `diff_productive`/`diff_summary` on each trajectory, and
  rejection sampling gains an opt-in `require_productive_diff` that drops "successes" which changed
  nothing — closing the #1 gap the M15 competitive study found across all five rivals (evolution
  with no fitness/verification signal).
- **Control-token stripping for untrusted content (M15-A3).** On top of the existing data-fence,
  the fetch path now defangs the chat-template families a page/document can embed to spoof a
  system/user turn or a tool call (`<|im_start|>`, `[INST]`, `<<SYS>>`, `<s>`, `<tool_call>`) with a
  visible placeholder; a tainted run also strips leaked control tokens from its finalized answer.
- **Tool-loop circuit breaker (M15-A4).** The agent loop stops a run that is physically spinning —
  identical-repeat, A-B-A-B ping-pong, or no-progress polling — instead of grinding to `max_steps`.
  Opt-out via `AgentConfig.detect_tool_loops`; conservative thresholds leave genuine runs untouched.
- **Fusion receipts (M15-B3).** Every fusion run can be priced into an itemized receipt — each
  advisor, the judge, and the synthesizer at its own model's rate — the substance behind "selective
  fusion with receipts". `fuse --show-cost` prints it, `fuse --receipt <jsonl>` persists it, and
  `fusion-receipts <jsonl>` summarizes an honest cost×quality curve (fusion rate, mean/total cost,
  dollars per passing answer). Tokens are measured, dollars estimated at list price; an unknown model
  prices to `unknown`, never a silent "free". See `docs/fusion-receipts.md`.
- **Checkpoint fork + paired A/B (M15-B1).** `RunCheckpointer.fork(src, dst)` branches a run's
  captured state so two policies can replay from the identical state (LangGraph "fork from a
  checkpoint"). `chimera/eval/paired.py` adds a paired (McNemar) comparison with a Wilson interval on
  the discordant pairs — a *tighter* CI than the unpaired Newcombe on the same data, so a real lift
  can reach significance at a smaller n. `bench-compare --paired` reports it; `run_paired_experiment`
  encodes "restore the forked state before each arm".
- **SKILL.md interop + progressive disclosure (M15-A2).** `chimera/skills/skill_md.py` parses/renders
  the open Agent Skills `SKILL.md` format (round-trips losslessly with `LearnedSkill`), with L1/L2/L3
  progressive disclosure (metadata → instructions → resources) as a token-cost lever, and
  provenance/taint carried in the frontmatter — a tainted skill is held `pending` on import. CLI:
  `skills-export` / `skills-import`.
- **Idempotency guard + memory sanitization (M15-A5).** Side-effecting tools
  (`send_email`/`http_post`/…) run at most once per identical call within a run, so a retry can't
  fire a duplicate email/payment; the recalled/evolved artifacts injected into context (lessons,
  skill cards, playbook) are control-token-stripped so a tainted memory can't spoof an instruction.
- **HITL {accept, edit, respond, ignore} envelope (M15-B2).** The taint-pause's binary approve/deny
  becomes a typed resolution: accept (finalize as-is), edit (finalize a corrected answer), respond
  (inject feedback and resume), ignore (deny). `RunCheckpointer.respond()`; CLI `solve --respond
  <thread> --feedback …` / `--edit <thread> --answer …`.
- **Failed→passed correction distillation (M15-B4).** When a task fails then passes, the verified
  (failed, passed) pair is distilled into an anti-pattern skill card — CrewAI's `train()` mechanic
  with the eval replacing the human. `SkillEvolver.distill_correction`, wired into the solve loop.
- **Maturity scorecard (M15-B5).** `chimera maturity` scores 7 surfaces × coverage-IDs, each proven
  by a real test (machine-derived; a renamed test shows as a gap). Doubles as a per-surface objective
  for the evolution loop (`Scorecard.weakest()`). The repo scores 37/37 = GA today.

## [0.4.1] - 2026-07-07

A reliability-and-speed patch for the headline feature (`chimera solve`), plus the honest
weak-model A/B harness that measures it. All changes were driven by running Chimera on the
official Terminal-Bench harness and inspecting where a weak model actually stalled.

### Fixed
- **The agent now *executes* the fix instead of narrating it.** `solve` used to accept any
  text-only reply as "done", so the worker would investigate, find the fix, and *describe* it
  ("you can run `git merge …`") without doing it. The system prompt now demands execution and a
  single nudge (`insist_on_action`) re-prompts an unexecuted plan to actually run. This is the
  core "does the feature work" fix.
- **No more 60s hangs on interactive commands.** Sandbox commands ran with an inherited stdin and
  no non-interactive environment, so `git commit` (opens an editor), credential prompts, `apt`, and
  `read` blocked until their timeout — one stall at a time, eating a whole step budget. Commands now
  run with stdin closed and `GIT_EDITOR=true`, `GIT_TERMINAL_PROMPT=0`, `PAGER=cat`,
  `DEBIAN_FRONTEND=noninteractive`, `CI=1`.
- `chimera version` (and `doctor` / the A2A card) reported a hardcoded `__version__` that had
  drifted to `0.3.0`. It now reads from the installed package metadata, so it can never drift from
  the release again.

### Changed
- The requirement checklist is now injected into the worker's **first** attempt (it used to only
  grade coverage *after* a failure), so multi-part tasks aim at every requirement from the start.
- Retry feedback now includes the **concrete failing test output**, not just the manager's note —
  the most actionable signal for the next attempt.

### Performance
- `solve` skips the redundant LLM coverage grade when an executable verifier (e.g. `pytest`) has
  already passed — the verifier is stricter ground truth. Measured ~30% less wall-clock on a cheap
  model (dominated by call latency), and runs finish cleanly instead of timing out.

### Added
- An honest, Docker-free weak-model-lift A/B (`bench/local_lift`): a `baseline` arm (raw model,
  one shot) vs the full Chimera loop on the same model + tasks, graded by each task's own `pytest`
  and scored with `chimera bench-compare` (Wilson + Newcombe CI). A recorded run and its honest
  (not-yet-significant) verdict are in `bench/local_lift/RESULTS.md`.
- A working Terminal-Bench installed-agent (`bench/terminal_bench`) that installs Chimera into the
  task container from an offline wheelhouse and runs `solve` — proven end-to-end on the official
  harness.

## [0.4.0] - 2026-07-06

The M14 cycle — from *"lift a weak model"* to *"prove it on a standard benchmark, then close the
loop so it keeps improving."* Four pillars: **proof** (a real measuring stick), **amplification**
(more ways to lift a weak model), a **closed self-improvement loop**, and **graded outcomes**.

Honest status: this ships the *measurement infrastructure* and the *capabilities*, not a published
benchmark number. A local, Docker-free proxy A/B was built and run on a cheap model, but with a
competent cheap model on small tasks the raw model already one-shots most of them (a ceiling
effect) — the lift lives in the hard-task regime the official benchmarks occupy, which needs a
Python 3.12 + Docker environment. The adapters below are wired and ready for exactly that.

### Added
- **Honest A/B engine** (`chimera bench-compare`) — the measuring stick every feature reports
  against: per-arm Wilson-bounded pass rates, the delta, and a Newcombe 95% CI; "significant" only
  when the CI excludes zero. Pure Python, no extra needed. Feed it two runs' pass/fail on the same
  task IDs (e.g. a free-model baseline vs the same model driven by Chimera).
- **Terminal-Bench adapter** (`chimera.eval.terminal_bench`) — a pure, unit-tested `chimera solve`
  command builder plus a lazy `terminal_bench.BaseAgent` subclass for the Harbor harness; the
  pass/fail verdict is the task's own tests, never self-reported. Opt-in `[bench]` extra.
- **SWE-bench Verified-Mini adapter** (`chimera.eval.swe_bench`, `chimera swe-bench-compare`) — the
  per-instance solve command + parsing of the official evaluation report, projected onto a shared
  instance list so both arms are scored on identical instances; reuses the A/B engine. Dataset and
  Docker harness stay opt-in and unbundled.
- **Requirement checklist** (`solve --checklist`) — extracts a task's atomic requirements once, then
  grades each attempt's coverage, catching the "must include / must not" constraints a weak model
  silently drops. Degrades to neutral on any parse error.
- **Agreement-based escalation** (`solve --fuse --agreement K`) — samples K cheap answers per turn
  and escalates to fusion only when they disagree: a free confidence signal (semantic agreement,
  no logprobs needed) that spends the expensive path only where the model is unsure.
- **Verifier-based sample selection** (`chimera.fusion.verifier_select`) — Weaver-lite: an
  independent judge scores N candidates and *picks* the best, rather than majority-voting; wired
  into self-consistency.
- **Independent strong verification** (`solve --strong-verify MODEL`) — a stronger, independent
  judge grades the final answer, but only on hard (already-retried) turns — dodging both
  self-enhancement bias and the cost of verifying every turn. A flaky judge fails open.
- **GEPA prompt evolution** (`chimera.evolution.gepa`, `chimera skills-evolve`) — reflective,
  Pareto-guided evolution of a skill's prompt: evaluate on a graded task set, reflect on a failing
  rollout to propose an improvement, keep a Pareto frontier (not just best-on-average), adopt only
  a measured lift. Native reimplementation, no external dependency.
- **ACE delta-playbook** (`chimera.evolution.playbook`, `chimera playbook`, `solve --playbook`) — an
  incremental strategy playbook edited only through deltas (add / reinforce / deprecate), never a
  monolithic rewrite, so hard-won detail is never erased (anti context-collapse, guaranteed by the
  code). Injected into the solve loop and curated from each run's outcome to close the loop.
- **RFT loop** (`chimera.ecosystem.loop`, `chimera evolve rft`) — rejection-sampling fine-tuning
  gated by the A/B bench: keep only successful high-reward runs, and promote a training round only
  when a candidate beats the baseline with a CI that excludes zero. No lift, no promotion; training
  stays external and opt-in.
- **Authorable rubric grading** (`chimera.eval.rubric_grade`, `chimera rubric-grade`) — weighted,
  task-authored criteria with a required-criterion veto, graded into a single outcome; `grade_batch`
  turns graded answers into the boolean trials the A/B engine consumes.
- **Local weak-model-lift harness** (`bench/local_lift/`) — a reproducible, Docker-free A/B over
  pytest-graded coding tasks (ground truth validated against reference solutions), clearly labelled
  as a local proxy, not the official leaderboards.

## [0.3.0] - 2026-07-06

The M13 cycle — the coding, intelligence, resilience and interop leap, under one thesis:
**make a weak/cheap model perform like a frontier one, with proof.** Panel fusion for combining
strong models; everything below for lifting a single weak one.

### Added
- **Surgical code editing** — `edit_file` (exact, unique-anchored replace, optional replace_all)
  and `apply_patch` (multiple SEARCH/REPLACE hunks applied atomically). The agent edits in place
  instead of rewriting whole files; a missing/ambiguous anchor is refused, not guessed. Both are
  WRITE + dangerous-when-tainted, so a poisoned run can't silently self-edit.
- **`read_document`** — ingest PDF/DOCX/PPTX/XLSX/HTML/CSV/EPUB as Markdown via MarkItDown
  (opt-in `[documents]` extra; an install hint instead of a failure when absent).
- **Repo-map** (`solve --repo-map`) — a structural table of contents (one line per Python file
  with its top-level symbols, via `ast`) folded into context, so the agent jumps to the right
  file instead of exploring blind. Prunes noise, honors `.gitignore`, bounded by a char budget.
- **Progress ledger** (`solve --progress-ledger`) — Magentic-One's inner loop: after a failed
  attempt a structured self-check (complete? progressing? next?) injects a concrete next-focus
  into the retry, so a weak model stops re-trying the same dead end. The verifier stays
  authoritative; any parse error degrades to neutral.
- **Completion contracts** (`solve --contract`) — declared, machine-checkable success clauses
  (`file_exists`, `file_contains`, `answer_matches`) as an AND gate on top of verify-or-revert;
  unmet clauses feed back so the next attempt fixes exactly what's missing. Catches the model
  narrating success it didn't achieve.
- **Dual-ledger re-plan** (`solve --replan`) — Magentic-One's outer loop: on a stall the
  `TaskLedger` records *why* it's stuck and the planner rebuilds from that accumulated cause, so
  the retry is fundamentally different, not the same plan reworded.
- **Self-consistency / best-of-N** (`fuse --best-of N`) — cheap single-model fusion: sample one
  model N times and take the consensus (or synthesize on a tie). Diversity from sampling, not
  from multiple providers.
- **Streaming** — `LLMGateway.stream()` token primitive + a typed `AgentEvent` vocabulary the
  autonomous loop emits through an optional sink; `solve --stream` shows live progress.
- **A2A `message/stream`** — the A2A server streams the task lifecycle over Server-Sent Events
  (working → completed), so a LangGraph/CrewAI orchestrator sees progress without polling. The
  agent card advertises `capabilities.streaming: true`.
- **Durable execution** (`solve --thread <id>`) — the solve loop checkpoints to SQLite after
  each failed attempt; a crash mid-run resumes from the last checkpoint on re-run, repeating no
  verified work. Terminal states clear the checkpoint.
- **Human-in-the-loop interrupt** (`solve --pause-on-taint`, `--approve`/`--deny <thread>`) — a
  run that consumed untrusted content pauses for sign-off before finalizing; approve finalizes
  the exact reviewed output (no re-run), deny drops it. The safety valve for the lethal trifecta.
- **Browser navigation** (opt-in `[browser]` extra) — a stateful `browser` tool drives a real
  Chromium via the accessibility tree (elements tagged with stable refs; click/type by ref, no
  vision model). Page content is data-fenced and the tool is a fetch-tool, so browsing taints
  the run.

### Notes
- New optional extras: `documents` (MarkItDown), `browser` (Playwright — also needs
  `playwright install chromium`). The core install stays light.

The security → adoption → intelligence → interop cycle: prompt-injection defenses with a
**measured** attack-success rate, out-of-the-box setup, real consumer recipes, measurable
memory & skills, and speaking the two winning agent protocols (MCP + A2A).

### Added
- **Prompt-injection defenses (measured, not asserted)** — a quarantined reader
  (dual-LLM / CaMeL: a tool-less model extracts only schema-validated fields from untrusted
  content), a taint-adaptive allowlist (dangerous tools narrow once a run is tainted),
  data-fencing/spotlighting on fetched content, and taint **provenance** on memories and
  learned skills (a skill distilled during a tainted run is held for review — the
  "Zombie Agents" anti-poisoning defense). A red-team suite (`chimera redteam`) reports the
  attack success rate with vs without defenses: **100% → ~14%** on the built-in corpus, and
  it *names* the remaining leak (exfiltration via an allowed tool) instead of claiming 100%.
- **`chimera init`** — one-command out-of-the-box setup: create `.env`, set a provider key,
  verify, and point at a real example.
- **Consumer recipes** — runnable `examples/`: `email_triage` (inbox → digest, read-only),
  `research_brief` (topic → sourced brief), `repo_watchdog` (run tests → health report),
  an examples index, and an MCP guide (`docs/mcp.md`) + `examples/mcp_github.py`.
- **Anti-stagnation signal** for the solve/evolve loop (crowding-score analog) + a
  multi-round continuous-evolution bench with **cost-drift** tracking (`chimera bench --rounds N`).
- **Per-skill metrics** (`chimera skills-stats`) with a measured retirement signal.
- **Router re-escalation** — a single-model turn that fails its check re-escalates to fusion.
- **Task-oriented docs site** (mkdocs-material) + a GitHub Pages workflow.
- **Memory-bench** (`chimera memory-bench`) — measures recall@k as memory grows, split into
  lexical vs paraphrase probes. Surfaces the honest keyword ceiling: exact-token recall holds
  at 1.00 even at 1000 facts, but paraphrase recall is 0.00 (no shared token to match).
- **Opt-in semantic memory recall** — `SemanticIndex` embeds facts + query and ranks by cosine
  so a paraphrase (`"physician"`) retrieves a fact about a `"doctor"`. Injected embedder
  (`LLMGateway.embed`), `CHIMERA_SEMANTIC_MEMORY` / `CHIMERA_EMBED_MODEL`; any embedder error
  falls back to the FTS/keyword path. `chimera memory-bench --semantic` measures the lift
  (paraphrase recall 0.00 → ~1.00 in-test).
- **Skill retirement** — a new `retired` status excludes an under-performing skill from
  retrieval while keeping it inspectable and reactivatable. `chimera skills-retire` acts on the
  retirement signal, proposed-with-review (dry-run by default, `--apply` to commit), never a delete.
- **Chimera as an MCP server** (`chimera serve --mcp`) — exposes `chimera_solve`, `chimera_fuse`,
  and `chimera_memory_search` as MCP tools over stdio, so any MCP client (Claude Desktop, an IDE,
  another agent) can call the whole engine as tools.
- **A2A adapter** (`chimera a2a-card`, `chimera serve --a2a`) — an Agent Card at
  `/.well-known/agent.json` + a JSON-RPC task lifecycle (`message/send`, `tasks/get`,
  `tasks/cancel`) at `POST /a2a`, so a LangGraph/CrewAI/AutoGen orchestrator can delegate a task
  to Chimera. Synchronous core (streaming/push not yet implemented).

### Fixed
- Migration: memory candidates resolving to the same file (case-insensitive filesystems)
  are deduped rather than listed/parsed twice.

### Added (M8 — daily-driver interfaces, first released here too)
- **`IsolatedCrew`** (`chimera crew-isolated`) — composes the three subagent primitives into
  one: tool-using workers each tackle the SAME task in their OWN git worktree, in parallel.
  Non-conflicting edits merge back; a file two workers both changed is reported as a conflict,
  not clobbered; a crashing worker fails only its own unit. **Per-worker verification**: pass a
  `verify` command (via `--verify`) run in each worker's worktree — a worker whose check fails is
  *rejected* (its edits discarded), so a broken change never lands. `IsolatedWorker(role,
  tools_factory, backend?)`; the tools factory roots each worker's registry at its isolated
  checkout. CLI: `crew-isolated TASK -W 'name:instruction' ... --verify CMD`. This is the
  division-of-labour counterpart to `solve-batch` (which runs N *separate* tasks): here N
  specialised workers split ONE task with real filesystem isolation. Verified live: two workers
  built modules in parallel worktrees; files both touched were correctly flagged as conflicts.
  **Optional synthesis** (`--synthesize`): a supervisor folds the merged workers' outputs (plus
  a note of any conflicts/rejects) into one unified final report (`IsolatedCrewResult.summary`).
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
