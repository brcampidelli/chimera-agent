# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.16.0] - 2026-07-09

**"Task-Typed Fusion & Fan-Out Safety."** The Tier-2/3 half of the same 8-category arXiv sweep that
produced v0.15.0 (M18) — six integrations that refine the aggregation and safety layers rather than the
verifier. All six map onto an existing component, are opt-in / degrade safely, and are model-free where
possible. Fusion learns to *vote* on logic tasks (not just synthesize); the blind panel gets a
regression guard and an independence metric; the autonomous loop gains auditable "why it failed" process
signals and a bug-report normalizer; PROBE's best-arm selection is wired into the live `solve` loop; and
a new aggregate monitor closes the collusion gap a per-worker monitor can't see under fan-out.

**Parked (honest — not in this release):** Dyserve blast-radius node weighting + residual re-solve
[2607.02942], exponential-weights judge weighting [2607.02247], skeptical-executor delegation clause
[2607.07097], hard token-cap + evict-completed-work [2607.07666], cache-locality-aware routing
[2607.02043], hot-worktree pool for the crew [2607.01415], and p95/p99 latency on the cost bench. None
is blocking; each maps to an existing component and can be picked up later.

### Added
- **Aggregate cross-agent monitor + collusion red-team** — Tier-2/3 from the arXiv sweep (FakeLab,
  arXiv 2607.07368). A per-worker monitor is **insufficient under fan-out**: work split across
  sub-agents can look benign per agent while the *aggregate* is malicious. The canonical case is split
  exfiltration — agent A fetches untrusted content (tainted in A's ledger), agent B execs it; B's own
  `TaintLedger` never saw the fetch, so B's per-action `assess_action` allows it. `AggregateMonitor`
  (`chimera/governance/aggregate_monitor.py`) sits one level up: given each sub-agent's capability
  events, it escalates **cross-agent-taint** (one agent consumed untrusted content and a *different*
  agent performed a sink — exec/escalation/executable-write) and **fan-out-volume** (aggregate sink
  count over budget though each agent stays under its own). Like the per-agent ledger it only escalates
  to *review*, never hard-blocks; pure/deterministic so an orchestrator feeds it `TaintLedger.events`
  after a run. Ships with a collusion red-team test that proves the per-agent monitor misses the split
  flow and the aggregate monitor catches it.
- **Bug-report task normalizer (`solve --normalize-task`)** — Tier-2 from the arXiv sweep
  (arXiv 2607.07593). A long, rambling bug report *hurts* an agent: narrative buries the few facts that
  matter, and the paper measured that trimming it — surfacing the salient fields up front — improves
  resolution. A deterministic, model-free normalizer runs **before planning**: when a task looks like a
  bug report and is long enough to ramble, it extracts the salient sentences into a structured header
  (location / error / expected-vs-actual / reproduce / fix-hint) and caps the original narrative. Only
  the planner/worker *prompt* is normalized — the raw task stays the identity for memory keys and the
  experience buffer, so a normalized run still dedups against the same task. Conservative: a non-bug or
  short task, or one with no extractable fields, is returned unchanged. `chimera/core/task_normalizer.py`.
- **TraceProbe anti-pattern detectors — auditable "why it failed" retry signals** — Tier-2 from the
  arXiv sweep (TraceProbe, arXiv 2607.06184). An outcome-only signal ("did verify pass?") never says
  *why* a hard attempt went wrong. Two cheap, deterministic detectors scan the per-step tool trace:
  **search-loop** (kept exploring — read/search/list/fetch — without ever editing or checking) and
  **verification-skip** (edited files but ran no test/verify/command to confirm the change). They wire
  **only into the failure retry-feedback** path of the autonomous loop — on an attempt that already
  failed verify-or-revert, they add advisory coaching for the next attempt (alongside the existing
  step-level fault hint), never a hard gate. `chimera/evolution/trace_probe.py`; operates on the
  ordered tool events the loop already extracts, so it's model-free and testable.
- **Panel-independence metric + blind-panel guard** — Tier-2 from the arXiv sweep (blind panel,
  arXiv 2607.02507, corroborating MALLM 2607.05477: *independent* generation before deliberation lifts
  quality; under pressure a non-blind panel's public/off-record answers diverged 3%→40%). Chimera's
  fusion panel is **already blind by construction** — each model answers the same prompt with no sight
  of the others — so this doesn't *add* blindness; it **formalizes and protects** it: a regression-guard
  test asserts no panel member's prompt ever contains another member's answer, and `FusionTrace.panel_diversity()`
  exposes the "panel independence" axis (mean pairwise dissimilarity of the panel's answers) so a run can
  see whether that independence actually paid off (high = genuinely different perspectives for synthesis;
  low = convergence, the cheap early-stop/vote territory).
- **Task-typed fusion aggregation — vote vs synthesize by task type (`CHIMERA_FUSION_TASK_TYPED`)** —
  Tier-2 from the arXiv sweep (MALLM, arXiv 2607.05477). The best way to *aggregate* a multi-model
  panel depends on the task: a single-verifiable-answer task (arithmetic, counting, multiple-choice,
  true/false) is best aggregated by **majority vote** — a correct minority answer must not be averaged
  away by a synthesizer, and it's cheaper — while an open-ended/knowledge task is best **synthesized**
  (judge → synthesizer, today's behaviour). Chimera already had both aggregators (`majority`, the
  judge/synth path); this adds the routing signal: a cheap, deterministic **lexical** task-type
  classifier (`chimera/fusion/task_type.py`, not a trained model) plus a `FusionEngine._aggregate` step
  that votes on a logic task **only** when the panel reaches a clear majority, else falls through to
  synthesis. Off by default and conservative — the sole behaviour change when enabled is "a logic task
  with a real panel majority returns that majority instead of synthesizing it." `FusionTrace` gains an
  `aggregation` field (`"synth"` | `"vote"`) for inspection.
- **PROBE live wiring — record (arm, proxy, reward) per attempt (`solve --probe-log`)** — closes the loop
  the v0.15.0 note flagged as "the natural follow-on." `ProbeLog` (`chimera/fusion/probe_log.py`) is an
  append-only JSONL that the autonomous loop writes to when `--probe-log` is set: each verified attempt
  records which **arm** ran (base worker vs escalated fusion worker), the cheap **proxy** (the manager
  self-judgment, computed unconditionally in probe mode so the pair is unbiased even on a passing
  attempt), and the expensive **reward** (the verified outcome). `chimera probe-select --from-log` then
  reads the accumulated observations and reports the δ-confident best arm — deciding from measured
  evidence whether escalation actually pays. Opt-in, best-effort (a telemetry write never fails a run),
  and the manager stays feedback-only — the executable verifier remains ground truth.

## [0.15.0] - 2026-07-09

**"Trustworthy Verifier & Write Security."** The M18 cycle, grounded in an 8-category arXiv sweep
(2026-07-09): five Tier-1 items that harden the weakest link in the "prove a weak model performs like
a frontier one" thesis — the verifier — plus write-side security and a measured skill-lifecycle loop.
Every item is an integration (never a reimplementation), opt-in, degrades safely, and is fake-tested.
The headline (M18-1) shipped **with a measured paired A/B and an honest retraction**.

### Added
- **PROBE — best-arm identification with a cheap-proxy control variate (`probe-select`)** — M18-5, from
  the arXiv sweep (arXiv 2607.06879). "Which model/config is best?" is a best-arm problem where each
  expensive reward (a real grade) pairs with a cheap proxy (a weak judge) of unknown correlation ρ.
  `ProbeBestArm` uses the proxy as a **control variate** so the estimate's variance scales by (1−ρ²) —
  fewer expensive draws when the proxy is good, and **unbiased when the proxy is useless** (β→0, it
  degrades to the plain reward mean with a wider interval, never a biased one). Returns each arm's
  adjusted mean ± interval, the δ-confident winner, and — if not yet confident — the arm to sample
  next. Pure + offline-testable; `chimera probe-select` runs it over recorded (proxy, reward)
  observations. `chimera/eval/probe.py`. (Live router wiring — recording proxy+reward per route — is
  the natural follow-on.)
- **Measured skill-lifecycle loop — provisional tier + auto promote/demote** — M18-4, from the arXiv
  sweep (arXiv 2607.07052, triple-corroborated; production −70% cost). Closes the loop Chimera half-had:
  it could *signal* under-performing skills but applied retirement manually and had no promotion tier.
  New skills can be born **`provisional`** (`CHIMERA_PROVISIONAL_SKILLS`, default off) — retrieved on
  probation so they earn a real track record — and `chimera skills-lifecycle [--apply]` runs a
  `SkillLifecyclePolicy` over the store's **measured** stats (never self-report): promote a provisional
  that proves itself (≥N uses, high win rate) to active, demote a failed-probation provisional or a
  regressed active skill to retired. Cron it for a hands-off promote/demote cycle. Default off = new
  skills stay `active`, so nothing changes until you opt in. `chimera/evolution/lifecycle_policy.py`.
- **Declared write-region for the file-writers (`solve --write-region`)** — M18-3, from the arXiv sweep
  (arXiv 2607.05483, PatchOptic). The workspace jail blocks writes *outside* the workspace, but nothing
  stopped a run from rewriting an *unrelated* file inside it — the injection→arbitrary-write attack ("a
  hostile page tells the agent to also update `config/secrets.py`"). A declared write-region (globs like
  `src/**,*.py`) makes `write_file` / `edit_file` / `apply_patch` **refuse** a write outside it, before it
  touches disk (fail-closed). Opt-in — an empty region keeps today's behaviour. Complements the taint
  ledger (which *escalates* such writes) with a hard capability boundary. `chimera/tools/write_region.py`.
- **Cross-provider, decomposed envelope auditing** — M18-2, from the arXiv sweep
  (arXiv 2607.00563 + 2607.06799). The `EnvelopeVerifier`'s spot check now (a) grades three named
  failure classes separately — **invented / dropped / contradiction** — instead of one holistic
  verdict (a single verdict under-discriminates), and (b) can run the auditor on a **distinct model**
  (`verifier_model` / `orchestrate --verify-model`), a cross-provider slug via the router, so a model
  doesn't grade its own family's output (measured: cross-provider auditing beats a same-model judge,
  and fine-tuned verifiers overfit). The auditor already re-derives its verdict from the raw artifact
  and never trusts the summary's self-report; this makes that posture explicit and independent.
  `chimera/orchestration/envelope_verify.py`, `hierarchy.py`. Back-compatible (legacy holistic verdicts
  still parse).
- **Spec-grounded test generation (`solve --gen-tests`)** — M18-1, from the arXiv sweep
  (arXiv 2607.06636). When `solve` has no `--verify` command, the fitness gate falls back to an LLM
  judging coverage — a proxy that rubber-stamps wrong code (a false positive that corrupts the gate).
  `--gen-tests` instead generates an executable pytest module grounded in the task's extracted
  atomic requirements and runs it as the gate, catching bugs the coverage grade misses. Opt-in;
  generated once then re-run on retries; any generation/run error degrades to a non-blocking pass
  (can only help, never falsely block). `chimera/core/spec_test.py`. **Measured** (paired A/B, goldilocks
  mistral-small-24b, n=6, `bench/local_lift/RESULTS.md`): gen-tests solved **3/6 vs 0/6** for the LLM
  coverage grade (Δ +50pp, 3-0 discordant pairs, CI [-6.1%, +50%] — not significant at n=6, reported
  as-is). Honest caveat: the win is a **resolve-rate** win (executable pytest feedback lets the weak
  model converge), not the false-positive reduction I pre-registered — that prediction was **retracted**
  (gen-tests had 1 false positive to coverage's 0, because the coverage arm never self-accepts anything,
  and a weak model can still write a shallow generated test).

## [0.14.0] - 2026-07-09

Integration-harmony pass. A full-repo audit (every tool, skill, CLI command, backend, and subsystem
checked for wiring) found the system largely coherent — pluggable backends conform, the hierarchy
orchestrator is wired end-to-end, the evolution loop's governance gate really fires, memory is read+
written with a taint gate, all modules import — but surfaced two real disconnects, now fixed.

### Fixed
- **The built-in skill library is now actually reached by the agent loop.** The skills (`echo`,
  `complete_code`, `fix_code`, `generate_script`, `data_analysis`, `data_visualization`) registered and
  listed via `chimera skills`, but nothing at runtime consulted them — `Agent` was tool-only and the
  `retrieve_relevant_skills` / `skills_context_block` helpers had zero callers. `Agent` now surfaces the
  few most task-relevant built-in skills into the system prompt (opt-out via
  `AgentConfig.inject_skill_context`), across every generic path at once. `chimera/core/agent.py`.
- **Skill retrieval no longer matches on stopwords.** The keyword scorer shared "the"/"of"/etc. with
  every skill description, so it surfaced irrelevant skills for any task (a geography question pulled the
  data skills). Now filters stopwords + sub-3-char tokens, so a match means a shared *content* word — a
  chart task surfaces `data_visualization`, a geography question surfaces nothing. `chimera/skills/retrieval.py`.
- **`download_media` is now a governed FETCH tool.** It pulls untrusted content from the internet but
  was in no governance set, so a downloaded file wasn't tainted and a later exec/read on it wouldn't
  escalate under `--taint`. Added to `FETCH_TOOLS`. `chimera/governance/ledger.py`.

## [0.13.1] - 2026-07-09

### Added
- **`data` optional extra** (pandas + scikit-learn + numpy) — the install path for the `data_analysis`
  skill's sandbox code, for parity with the new `viz` extra. Surfaced by a live end-to-end test:
  `uv run chimera run "use data_analysis: …"` needs these importable in the sandbox; the extra makes
  `pip install 'chimera-agent[data]'` the one-liner. (The skill already degraded to a clear ImportError
  without them; this just names the dependency.)

## [0.13.0] - 2026-07-09

Data visualization, the honest way. Studied plotly / bokeh / seaborn / altair / matplotlib — all are
frameworks or JS-backed renderers an agent should *call*, not reimplement (bokeh is ~half TypeScript,
plotly wraps plotly.js, matplotlib's renderer is C++, seaborn wraps matplotlib). So Chimera gets two
complementary capabilities, neither a reimplementation.

### Added
- **`data_visualization` skill.** Sibling of `data_analysis`: writes a self-contained chart script for
  the `execute_code` sandbox — matplotlib/seaborn for static PNG/SVG, plotly for interactive HTML — with
  the headless-backend gotcha (`matplotlib.use("Agg")` before pyplot) and save-to-workspace-then-print-
  the-path discipline baked into its prompt. Covers arbitrary/custom charts. `chimera/skills/builtin/data_skills.py`.
- **`render_chart` tool — declarative Vega-Lite → chart, safely.** Altair's insight applied to an agent:
  a Vega-Lite spec is **inert, inspectable JSON data, not code** — a stronger governance story than
  executing generated plotting code. Renders **HTML with zero extra deps** (embeds the spec + the Vega
  CDN); **PNG/SVG** via the optional `viz-vega` extra (`vl-convert-python`). Shape-validated before
  render. `chimera/tools/chart.py`.
- Optional extras: **`viz`** (matplotlib + seaborn + plotly for the skill's sandbox code) and
  **`viz-vega`** (vl-convert-python for static Vega-Lite rendering).

### Notes
- Honest scope, again: the code sandbox already imports matplotlib/plotly/seaborn — the skill just names
  the capability and handles the headless discipline; it does not vendor or reimplement any of them.
  Vega-Lite earns a dedicated tool only because its artifact is safe declarative data.

## [0.12.0] - 2026-07-09

Rounds out the media + data capabilities, all in the honest "orchestrate, don't reimplement" spirit: a
named data-analysis skill, a robust media downloader, and an optional fully-local image backend.

### Added
- **`data_analysis` skill.** Names the way an agent actually "does ML": given a task + dataset it writes
  a self-contained pandas + scikit-learn script (load → explore → model → evaluate) that the agent runs
  in the `execute_code` sandbox. Orchestration, not a reimplementation of sklearn.
  `chimera/skills/builtin/data_skills.py`.
- **`download_media` tool.** Download a video (or just its audio, mp3) from YouTube + 1000+ sites into
  the workspace. Wraps **yt-dlp** — not pytube, which is single-site and perpetually breaks — so it
  survives player/cipher/age-gate churn. Opt-in `media-dl` extra; audio extraction needs ffmpeg.
  Pairs with `transcribe_audio` (download → transcribe → summarize). `chimera/tools/download.py`.
- **Local image backend for `generate_image`.** Set `CHIMERA_IMAGE_BACKEND=local` (or leave `auto` with
  no OpenAI key) to run **FLUX.1-schnell** (Apache-2.0) via `diffusers` fully offline — the opt-in,
  GPU-heavy `imagegen-local` extra. The hosted OpenAI path stays the default. Chimera *runs* a diffusion
  model; it does not train one. `chimera/tools/media.py`.

### Notes
- Honest scope (studied pytube / CogVideo / OpenCV): pytube → wrapped yt-dlp instead (more robust);
  **CogVideo** (video generation) deliberately **not** vendored — a heavyweight trained model, hosted-API
  territory if ever needed; **OpenCV** needs no dedicated tool — the agent already `import cv2`s in the
  code sandbox.

## [0.11.0] - 2026-07-09

Scraping polish (deterministic CSS extraction + resumable crawl) and a new **speech-to-text** tool —
the symmetric partner to image-gen + TTS. Also an honest scope note: Chimera *orchestrates* models
(Whisper/Stable Diffusion/PyTorch/OpenCV are called or run in the code sandbox), it does not reimplement
them.

### Added
- **Deterministic CSS-selector extraction before the LLM.** `extract` accepts `selectors` (field → CSS,
  e.g. `{"price": ".price", "link": "a.more::attr(href)"}`): for a known page template those fields are
  pulled with BeautifulSoup — free, exact, no LLM — and the safe quarantined LLM fills only what a
  selector missed (the crawl4ai cheapest-tool-first idea). Reuses the `bs4` that ships with the
  `documents` extra; falls back to the LLM path when absent. `chimera/scrape/extract.py::extract_by_css`.
- **Resumable crawl.** `crawl` checkpoints its frontier + visited set to disk after every page (atomic
  write) and appends pages to a `.jsonl` sidecar, so a crawl interrupted at page N resumes from N+1
  (`resume=true` by default; `limit` is the total target across resumes). State auto-clears when the
  crawl completes. `chimera/scrape/crawl.py`.
- **`transcribe_audio` tool — speech-to-text.** Chimera *orchestrates* Whisper (it doesn't train an ASR
  model): local **faster-whisper** when the new `stt` extra is installed (offline/private), else the
  hosted OpenAI Whisper API. Fills the obvious gap next to the existing image-generation and
  text-to-speech tools. `chimera/tools/media.py`; in `READ_TOOLS`.

## [0.10.0] - 2026-07-09

Phase 2 of the web verb set: whole-site **`map`** and **`crawl`**, completing the
`scrape / extract / map / crawl` quartet — all built in, robots-aware, no new dependency.

### Added
- **`map` tool — list a site's URLs cheaply.** Reads the sitemap (robots.txt `Sitemap:` lines + the
  conventional `/sitemap.xml`, one index level deep), falling back to scanning the page's same-domain
  links. Optional `search` keyword filter. `chimera/scrape/crawl.py::map_site`.
- **`crawl` tool — BFS across a site.** Follows links from a seed URL and returns each page's clean
  Markdown, bounded by `limit` + `max_depth`, same-domain by default, deduped, with `include`/`exclude`
  URL glob patterns. **robots.txt-aware**: obeys `Disallow` and `Crawl-delay` by default (an ethical
  posture per the IBM/robots analysis; opt out with `respect_robots=false`). Reuses the Phase-1
  cost-aware fetch cascade. `chimera/scrape/crawl.py::crawl_site`. Both tools are in `FETCH_TOOLS`.

## [0.9.0] - 2026-07-09

The **web scraping + safe structured extraction** release — an agent-native answer to
Firecrawl/crawl4ai/ScrapeGraphAI, built on what Chimera already ships (browser + MarkItDown + the
quarantined reader), with no new dependency and no external service by default. Phase 1 of a
`scrape / extract / map / crawl` verb set (map + crawl come next).

### Added
- **`scrape` tool — any page → clean Markdown + metadata.** A cost-aware fetch cascade
  (`chimera/scrape/fetch.py`): plain HTTP first, escalate to the built-in browser (JS render) when the
  result is empty, and — only if `FIRECRAWL_API_KEY` is set — fall back to Firecrawl for heavy anti-bot
  pages. `render=http|browser|firecrawl` forces a backend; `include_links` returns the page's links.
  Cleaning reuses the MarkItDown seam with a stdlib text fallback (`chimera/scrape/clean.py`).
- **`extract` tool — schema → validated JSON, injection-safe.** Chimera's edge over other scrapers:
  instead of feeding the page straight to the extraction LLM (which a page can prompt-inject), `extract`
  routes through the **quarantined reader** (a tool-less, schema-validated model), so a hostile page can
  at worst return a wrong value — never a new instruction or tool call. Large pages are chunked and
  merged map-reduce style, short-circuiting once every field is filled to cap cost
  (`chimera/scrape/extract.py`). Both tools are in `FETCH_TOOLS` (data-fenced, taint the run).
- **Optional Firecrawl passthrough** (`chimera/scrape/firecrawl.py`, `FIRECRAWL_API_KEY`): used only for
  pages the built-in engine can't fetch — an honest "use their infra only when needed", never a hard
  dependency. Unset the key and it's never touched.

## [0.8.1] - 2026-07-09

### Fixed
- **Docker image bakes the browser so it launches in the container.** 0.8.0 makes Playwright a core
  dependency, but on a slim base image the Chromium *system libraries* are missing, so the binary
  downloads yet won't launch ("browser has been closed"). The `Dockerfile` now runs
  `python3 -m playwright install --with-deps chromium` at build time — installing Chromium **and** its
  OS libs — so `docker build` / `docker compose` users get a working browser out of the box (and skip
  the ~150MB first-use download). Adds ~400MB to the image; comment the line out to opt out.

## [0.8.0] - 2026-07-09

**The browser is now built in.** Playwright moved from an opt-in extra into the core dependencies, so
every `pip install chimera-agent` (and every clone) ships the full web browser
(navigate/click/type/read_text/find) — no extra to remember. pip can't ship Chromium itself, so the
browser tool **auto-downloads the Chromium binary (~150MB) on first use** (one-time; opt out with
`CHIMERA_BROWSER_AUTO_INSTALL=0` and run `playwright install chromium` yourself). The `[browser]` extra
still resolves (now a no-op alias) for back-compat. `read_text` clean-Markdown still uses the optional
`documents` extra, falling back to plain text without it.

## [0.7.0] - 2026-07-09

The **front-end reading + honest-benchmark** release. The browser can now *read* rendered pages
(clean Markdown), not just drive them; prompt caching is used and measured; a transfer-holdout gate
guards self-evolution against negative transfer; delegation is routed by the measured (D−1)/D curve;
and the official Terminal-Bench A/B is published as a pre-registered null — including a public
self-correction once the control arm was measured. All additive; nothing breaking.

### Added
- **Read the front-end, not just the back-end.** The `browser` tool (Playwright/Chromium)
  gained two actions for *reading* a rendered page, beyond driving its interactive
  elements: **`read_text`** returns the page's full rendered text — clean Markdown via
  MarkItDown when the `documents` extra is present, plain visible text otherwise — with an
  optional `url` to open+read in one step; **`find`** searches that rendered text for a
  query. This closes the gap where the agent could navigate/click a page (accessibility
  tree) but couldn't actually *read* an article/doc/result that raw `http_get` misses on
  JS-heavy sites. Output stays data-fenced and taints the run (already in `FETCH_TOOLS`).
  New `CHIMERA_BROWSER_HEADLESS` toggles headful Chromium for debugging. Reuses the
  existing MarkItDown seam; no new dependency. Docs: `docs/recipes.md` (browsing + a
  "Researching a topic: search + read" recipe) and `examples/research_brief`.
- **Transfer-holdout promotion gate.** A self-evolution change (RFT round / GEPA / ACE / skill)
  must not regress a disjoint, same-capability holdout slice before it's promoted, not just win
  its tuned slice — closing a "memorized the eval" blind spot. `chimera/eval/transfer.py`
  (paired) + a holdout arm in the RFT loop (`baseline_holdout`/`candidate_holdout`, blocks on a
  confident CI-negative regression). Motivated + cited: EvoAgentBench (arXiv 2607.05202) measured
  ungated evolution producing negative transfer (GEPA −12.3).
- **Prompt caching, used and measured (M17).** `prompt_cache` (opt-in) marks the stable
  system prefix with a provider cache breakpoint so the single agent and worker fleet
  reuse it at the cache read rate; the gateway captures `cache_read`/`cache_write` tokens
  and threads them through the token budget into delegation receipts, turning the
  token-economy "dollar caveat" into continuous measurement. Providers that cache
  automatically (OpenAI, DeepSeek) are left untouched. Honest status: verified the
  capture path on DeepSeek (`cache_read` 0→5 on a repeat), but a live OpenRouter→Anthropic
  probe did **not** surface caching (litellm's OpenAI-compat routing doesn't forward
  Anthropic content-block `cache_control`) — documented as a known limitation; the native
  `anthropic/…` provider is the path that populates it.
- **Curve-driven delegation gate.** `count_sources()` — 2+ distinct sources + read intent
  routes to the hierarchy (the measured (D−1)/D guaranteed-gain region) and skips the
  crude profitability veto.
- **Companion suite, 3 axes + robustness + a caching dollar model.** `bench/hierarchy_sweep`
  now sweeps D / S (doc size) / Q (turns); real runs confirm the (D−1)/D law across
  D=2..5; `chimera/eval/cache_cost.py` models how caching narrows (and can invert) the
  dollar win; `make_hetero_task` proves the law is size-distribution-agnostic.

### Fixed
- Hierarchy code-review pass: verification is now enforced (a rejected worker result
  can't reach synthesis); budget honesty for partial provider usage; `_conflicting`
  matches its contract; concurrent-worker robustness; thread-safe `CompletionCache`.
- **Terminal-Bench adapter: two integration bugs, each fixed on a real green pass.**
  `bench/terminal_bench/chimera_installed_agent.py` now runs the solve in `/app` (TB's
  client container works there and tests assert absolute `/app/...` paths — `exec_run`
  previously defaulted to the image WORKDIR, so files landed where the grader never
  looked), and install is a network-first bootstrap chain (PyPI → ensurepip →
  urllib-fetched get-pip → `--break-system-packages` → offline wheelhouse) that
  tolerates the harness's heterogeneous base images (no pip, PEP-668, no curl/wget).

### Benchmarked (honest, published as measured)
- **Official Terminal-Bench A/B, N=40, pre-registered.** `bench/terminal_bench/RESULTS.md`:
  baseline (bare model, 1 attempt) 7.5% vs chimera (repo-map+ledger+checklist scaffold,
  1 attempt) 2.5%, Δ −5.0pp, 95% CI [−5.0%, +1.6%], not significant. The pre-registered
  prediction's *direction* was wrong — the scaffold did not beat the baseline on this
  single-attempt run. Investigated (not resolved): a `hello-world` pass-to-fail flip vs.
  the Phase-1 probe, consistent with LLM run-to-run variance or a concurrency artifact.
  Disclosed gaps: no token/cost telemetry from this adapter; `--max-attempts 1` both arms
  means this doesn't test Chimera's retry-loop lift mechanism (which has a separate,
  positive paired result in `bench/local_lift/RESULTS.md`, +50pp on a goldilocks-weak
  model). Published per the project's honest-benchmark discipline: register before
  running, publish regardless of outcome, never re-run to chase significance.
- **Terminal-Bench follow-ups (pre-registered, `RESULTS.md`).** (A) Anomaly repeat, BOTH
  arms 5× serial: `hello-world` chimera 2/5 vs baseline **0/5**, `fibonacci-server` 0/5
  both, `fix-permissions` 5/5 both → the Phase-2 pass→fail flip is intrinsic run-to-run
  variance, and under control the scaffold is **≥** the bare model on the very tasks that
  drove the discordant pairs (an earlier "the scaffold false-fails easy tasks" read, from
  the one-armed measurement, was refuted by the baseline arm and retracted). (B)
  `--max-attempts 3` A/B (retry-loop mechanism, same 40 slice, disclosed non-leaderboard
  timeout override): baseline 2.5% vs chimera 5.0%, Δ +2.5pp, CI [−1.5%, +2.5%], not
  significant — direction matched the prediction (the loop recovered `oom`, lost nothing)
  but at 1-vs-2 passes it's the noise floor. The baseline itself moved 7.5%→2.5% between
  runs, so at this pass-rate floor N=40 is variance-dominated and no delta (either sign) is
  separable from noise; the signal-bearing regime for Chimera's lift stays the goldilocks
  paired run in `bench/local_lift`.

## [0.6.0] - 2026-07-08

The **M16 "Hierarchy & Second Brain"** cycle — vendor-agnostic hierarchical orchestration with
token economy, plus the first daily-driver assistant features on top of it. Grounded in three
studies (Anthropic orchestrator-worker, FrugalGPT/RouteLLM tier routing, MAST failure taxonomy)
and one rejected idea (pxpipe's image-arbitrage — kept only its measurement discipline). Every
model role (weak/mid/top) accepts any LiteLLM/OpenRouter slug from any vendor; the user pins models
or picks a cost mode (`cheap|balanced|premium|auto`, auto entering at the mid tier).

### Added
- **Model tiers + cost modes + multi-vendor catalog (M16-A1).** `weak_model`/`mid_model`/
  `orchestrator_model` (vendor-agnostic) + `cost_mode` in `Settings`; `chimera/providers/catalog.py`
  curates suggestions across DeepSeek/GLM/Kimi/OpenAI/Google/Anthropic/Qwen/Meta/Mistral as DATA
  (slug + approx price + tool-calling notes). `resolve_tiers`: explicit pin > cost mode > default.
  CLI: `chimera models` (ladder + catalog + `set <role> <slug|mode>`); `chimera init` asks the mode.
- **Delegation contract (M16-A1).** `orchestration/spec.py`: `TaskSpec` down (objective/format/
  schema/boundaries/effort), `ResultEnvelope` up (bounded summary + artifact refs + gaps — never a
  transcript); `validate_envelope` free schema gate.
- **Artifact store + envelope compaction (M16-A2).** Content-addressed per-run store; `build_envelope`
  spills bulk to disk and returns a distilled summary + refs (small results untaxed). Measured: parent
  receives ≤15% of raw chars on bulky transcripts, full fidelity retained.
- **Delegation receipts + profitability gate (M16-A3).** Measured tokens/cost per delegation WITH the
  inline counterfactual in the same row; `estimate_profitability` stops unprofitable delegations;
  `:free` slugs price as measured-zero. `chimera delegations` reports net saving (or OVERSPENT).
- **Harness-enforced token budgets (M16-A4).** `TokenBudget`/`BudgetedBackend` (hard/soft/count-only)
  + `EffortPolicy`; a runaway backend provably halts under budget. chars/4 fallback always flagged.
- **Three-gate envelope verifier (M16-A5).** schema (free) → acceptance criteria (deterministic) →
  probabilistic spot-check that pulls the raw artifact into the VERIFIER's context only.
- **FrugalGPT cascade (M16-A6).** `CascadeBackend`: weak→gate→mid→gate→fusion (tool turns → mid;
  policy-flagged turns skip weak; auto enters at mid). `route_log` persists every decision (prompt as
  hash only) as future router training data. `--cascade` on chat/solve; `chimera cascade-bench` (4 arms).
- **HierarchicalOrchestrator (M16-A7).** Deterministic shape classifier (write/simple → single-agent
  fallback, audited); top-model decompose (JSON, one repair) → parallel budgeted mid workers under
  contract (byte-identical cached system prefix, no mid-task model rotation) → verifier → synthesis
  over summaries only (fusion only on conflict). `chimera orchestrate [--dry-run]` + `delegations`.
- **Hierarchy paired A/B (M16-A8).** `eval/hierarchy_ab.py` + `bench/hierarchy/`: single-agent vs
  orchestrator-worker on the same model, paired McNemar/Wilson on quality, measured totals on tokens
  (no cost-significance claims), predictions registered before running, negative control asserted.
- **Persistent user profile (M16-B1).** Byte-stable cacheable preamble (`interface/profile.py`);
  `chimera profile show|set|forget`; wired into chat/tui/assist.
- **`chimera assist` (M16-B2).** Daily-driver on the cascade: cheap by default, `/task` full-power
  lane, `/profile` mid-conversation, memory+nudges+consolidation on, per-session cost receipt on exit.
- **Morning brief (M16-B3).** `chimera brief` — one budgeted worker per topic in parallel, top-tier
  synthesis; `examples/morning_brief/` with scheduling recipes + honest-cost section.

### Measured (real runs, predictions registered before running, both wins AND losses published)
- **Fusion costs 11× for the same answer.** `bench/cascade` (12 reasoning tasks, deepseek mid):
  mid tier 100% at 846 tokens; full fusion also 100% — for 9,526 tokens. The measured justification
  for reserving fusion behind the cascade (which hit ~mid quality at ~1/12 of fusion's tokens).
- **Orchestration's token economy is regime-specific — and we published the losing run.**
  `bench/hierarchy` (single-shot, small docs): the hierarchy cost **+47% MORE** tokens (prediction
  of ≥30% reduction FAILED, reported plainly). `bench/hierarchy_multistep` (multi-step, large docs):
  it cost **−66.5% FEWER** tokens at identical 100% quality. Together: fan-out only pays when a single
  agent would re-send large context across many steps — which is exactly what `chimera orchestrate`
  gates for.

### Fixed (post-merge code review of the cycle)
- **Envelope verification was computed but not enforced** — a verifier-rejected worker result could
  reach synthesis if the bounded re-ask also failed. Now a result is folded in ONLY if it passes
  verification; if all workers are rejected the orchestrator recovers via the single-agent fallback.
- **Budget honesty:** partial provider usage (`prompt=1200, completion=None`) was counted as exact;
  now any missing side triggers the chars/4 fallback and the `estimated` flag.
- `_conflicting` now matches its contract (pairwise term overlap AND a marker) — no false-positive
  fusion on a lone "however", no false-negative on real same-topic disagreement.
- Worker provider errors no longer abort the whole batch or lose siblings' receipts; `CompletionCache`
  is thread-safe with atomic writes (the hierarchy dispatches workers concurrently).

## [0.5.0] - 2026-07-07

The **M15 "Proved and Governed"** cycle — built from a real reverse-engineering study of five
competitors (OpenClaw, Hermes, nanobot, CrewAI, LangGraph). Their code shares the same three gaps,
which are exactly Chimera's bets: **fitness-signaled evolution, architectural security, and honest
benchmarks**. This release doubles down on all three, stealing the best idea from each rival and
adding the honesty layer none of them have.

Highlights: diff-gated evolution (never trust a self-reported success), per-advisor **fusion cost
receipts** ("selective fusion with receipts"), a **checkpoint fork + paired A/B** that measurably
tightens the confidence interval, SKILL.md interop, control-token stripping + idempotency guards,
a tool-loop circuit breaker, a typed HITL {accept, edit, respond, ignore} envelope, failed→passed
correction distillation, a maturity scorecard, an error→recovery taxonomy with credential cooldowns,
a scored memory-promotion gate, and `doctor --fix`. A recorded weak-model paired run on a
"goldilocks" model shows the full loop tripling the pass rate (17%→67%, 3–0 on discordant pairs),
reported honestly (one pair short of significance — no p-hacking). 1020 tests, ruff + mypy strict.

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
- **Paired weak-model bench runner (M15-C1).** `bench/local_lift/run_paired.py` runs baseline vs the
  full loop from the identical restored state per task and reports the paired verdict; a recorded
  goldilocks run (mistral-small-24b) shows the loop tripling the pass rate, one pair short of
  significance — recorded honestly. See `bench/local_lift/RESULTS.md`.
- **Error→recovery taxonomy + credential cooldowns (M15-C2).** `chimera/providers/failover.py`
  classifies a completion error and maps it to rotate-key / fallback-model / abort; a `CredentialPool`
  rests a rate-limited or revoked key for a TTL instead of hammering it. Wired into the gateway's
  fallback loop — abort short-circuits instead of trying every key and model first.
- **Dreaming promotion gate (M15-C3).** `chimera/memory/dreaming.py` promotes a recall to durable
  memory only when it clears weighted thresholds (min score, recall count, distinct queries, recency
  half-life, age) — derived from measured usage counters, never from an LLM's say-so.
- **`doctor --fix` (M15-D1).** Auto-repairs safe setup issues (creates the state dir, scaffolds a
  `.env` from `.env.example`) — never writes a secret, never clobbers an existing `.env`.

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
