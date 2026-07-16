# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.33.0] - 2026-07-16

### Added
- **Intel Mac support.** `macos-latest` is arm64-only, so until now an Intel Mac literally could not
  install the native app (an aarch64 `.dmg` won't run) and got no updater entry either. The release
  matrix now also builds on `macos-15-intel`, publishing an x86_64 `.dmg` + a signed `darwin-x86_64`
  updater artifact — so Intel Macs both install and self-update. (The frozen PyInstaller sidecar is
  arch-specific, so a "universal" Tauri build wouldn't have fixed this: the backend has to be built
  ON an Intel runner.) Both macOS jobs emit a plainly-named `Chimera.app.tar.gz`, so the arch is now
  stamped into it before upload — otherwise the two jobs' assets would collide and both manifest
  entries would point at one file. **Note:** `macos-13` was retired in Dec 2025 and `macos-15-intel`
  is the last x86_64 image GitHub Actions offers (scheduled to go away ~Aug 2027).
- Desktop app: a **frontend component test suite** (Vitest + Testing Library + jsdom), run in CI's
  `desktop` job before the build. Until now `tsc --noEmit` + `vite build` were the only guards, so a
  UI regression could ship silently. The 50 tests assert user-visible behaviour on the screens where
  a regression would be dishonest rather than merely ugly: `VersionBadge` (an update is signalled
  only when confirmed; an offline/failed check shows nothing; a dismissed version is persisted and
  never re-prompted), the `Code` run receipt (real `verify_output` shown only when non-empty; a
  reverted attempt labelled attempted-and-undone, never applied; a cancelled run reported as
  cancelled, not failed), plan preview (previewing makes no run; the approved/edited plan is what
  reaches the run request; the plain Run path injects no plan), cooperative Stop, the git panel's
  non-repo empty state and explicit-path commit, and `Agents` (per-task pass/fail, prominent
  conflicts, and the "ran in-place WITHOUT isolation" banner outside a git repo).
  Run with `npm --prefix apps/desktop run test`.

## [0.32.2] - 2026-07-16

### Fixed
- Updater manifest: the Linux entry in `latest.json` pointed at the `.deb` instead of the `.AppImage`
  (Tauri signs the deb too, and the fragment step's `.sig` search picked it first). The Linux updater
  installs the AppImage, so a deb URL would make Linux auto-update download an un-appliable package.
  The `.sig` search now excludes `deb/`; Windows/macOS were already correct. Verified against the live
  published `latest.json`.

## [0.32.1] - 2026-07-16

### Fixed
- CI: the macOS updater artifact needs the `app` bundle target (`--bundles app,dmg`) so
  `createUpdaterArtifacts` emits the signed `.app.tar.gz(.sig)`; without it the v0.32.0 release's
  macOS fragment step failed and no `latest.json` was published. This patch re-runs the full signed
  pipeline so the updater manifest ships — the in-place auto-updater is now live.

## [0.32.0] - 2026-07-16

### Added
- **Native in-place auto-update (desktop app).** The Tauri desktop shell now checks GitHub on startup
  for a **signed** update (Minisign signature verified against the app's embedded public key — never
  disabled) and, only if one exists, shows a native confirm prompt ("A new version (vX.Y.Z) is
  available. Download and install now? The app will restart."). On accept it downloads, verifies,
  installs and relaunches; on decline, or on any error (offline, no release, rate-limit, bad
  signature), it stays silent — no nag, no crash, never a silent install. CI signs the updater bundles
  with the repo's `TAURI_SIGNING_PRIVATE_KEY` secret and publishes a `latest.json` manifest to each
  GitHub Release. This complements — does not replace — the existing pip/web "update available" signal
  (`VersionBadge` + `/api/version`), which stays for pip users. *Activates for users on the **next**
  release onward* (a build must ship the updater client + a `latest.json` before an install can find an
  update).

## [0.31.0] - 2026-07-15

### Added
- **Update-available signal + prompt (desktop app chrome).** A low-key version indicator sits in the
  bottom corner (like the Hermes app's) showing the running `v{version}`. On load the app asks the new
  `GET /api/version`, which checks GitHub's public releases API for the newest tag and returns
  `{version, latest, update_available, notes_url}`. When — and ONLY when — a **strictly-newer** release
  is confirmed, the indicator becomes an accent "**v{latest} available**" pill that opens a small
  dismissible prompt: a link to **View release** and a copy-able `pip install -U 'chimera-agent[desktop]'`
  line. *Honest by construction:* offline or on any error (timeout, rate-limit, parse) the check
  degrades to the quiet current version — it can never show a false "update available". Dismissing a
  version persists in `localStorage`, so a version the user chose to skip doesn't nag every launch.
  Wiring: `chimera/api/version_api.py` fetches with stdlib `urllib` on a worker thread (short timeout,
  a `User-Agent` header) and caches the result for an hour so repeated GETs don't hammer the API; the
  version comparison parses `MAJOR.MINOR.PATCH` into int tuples (so `0.9.0 < 0.10.0`, not a string
  compare). It's a plain GET of the public releases API — no user data is sent.
  - **Future work:** the Tauri in-place auto-updater (download + install the new version from within
    the app). This change is signal-only — it links to the release and shows the pip command, it does
    not install anything.
- **Browser screenshot verification artifact (desktop "Code" screen).** A new **"Verify in browser"**
  panel: type a URL, hit **Capture**, and the headless browser navigates there and saves a real
  full-page PNG that is stored server-side (`<home>/artifacts/<uuid>.png`) and shown inline. It is
  *honest by construction* — a screenshot of exactly the URL you gave, captioned as such; it never
  claims the agent autonomously verified anything. If the browser runtime is missing (or the page
  fails to load) it degrades to the honest install hint (`Browser not installed — run: playwright
  install chromium`), never a placeholder image. Wiring: the `browser` tool gains a `screenshot`
  action (full-page PNG to a path; the agent can use it during a run too); `POST /api/verify/screenshot`
  captures on a worker thread and returns `{ok,id,error}` (a clean 200 on failure, never a 500); the
  PNG is served by `GET /api/artifacts/{id}`, whose id is validated against a strict hex-only allowlist
  (`^[0-9a-f]{8,64}$`) plus a path-containment check, so it can never become an arbitrary-file read.
  - **Future work:** session recording / video capture, and autonomous self-verification during a run
    (this MVP is a user-driven capture of a URL the user provides).
- **Cooperative Stop for a single coding run (desktop "Code" screen).** While a run is in flight, a
  **Stop** button appears next to Run. It cancels *cooperatively*: an in-flight model call cannot be
  interrupted (the step is blocking), so the run halts **before its next attempt**, after the current
  attempt finishes — the UI says exactly that (button tooltip + a "Stopping after this attempt…"
  state), never implying an instant kill. Wiring: `AutonomousAgent` gains an optional `should_stop`
  probe checked at the top of each attempt (default `None` ⇒ byte-identical to before); `POST /api/runs`
  now emits a leading `run` frame carrying the run id and threads a per-run stop `Event` down; a new
  `POST /api/runs/{run_id}/cancel` sets it (unknown/finished id ⇒ honest `{ok:false}`, 200, a no-op).
  A cancelled run returns `AutonomousResult(success=False, stopped_reason="cancelled")`.
  - **Future work:** per-task cancel in the parallel Agent Manager batch, and mid-run *steer* (this
    change is cancel-only — no steering).

## [0.30.0] - 2026-07-15

### Added
- **Desktop "Agent Manager" screen — parallel isolated multi-task runs.** A new screen (and
  `POST /api/agents` SSE endpoint) that runs SEVERAL coding tasks concurrently, each in its **own git
  worktree** — the same `chimera solve-batch` isolation machinery reused end to end. A live board shows
  one card per task (status streamed from per-task-tagged events), then each task's real result: pass/
  fail, attempt count, changed files, and a collapsible per-file diff — all from the actual
  `AutonomousResult` (nothing fabricated). A batch summary bar surfaces the merged-file count and,
  prominently, the **real cross-task conflicts**: files two or more successful tasks both changed are
  left **unmerged** (neither version silently wins) and listed for you to resolve, never hidden.
  - **Git-only isolation, stated honestly.** Isolation is real only inside a git repo; outside one the
    tasks run **in-place with no isolation** (concurrent edits can collide, conflicts can't be detected).
    The endpoint returns `is_repo`, and the UI shows an explicit banner when it's false — it never
    implies isolation happened when it didn't.
  - The SSE `batch_done` shape is exposed to OpenAPI (and the generated TS types) via a companion
    `GET /api/agents/schema`, mirroring how the run-receipt types reach the schema through `GET /api/runs`.
  - This MVP has **no mid-run cancel/pause/steer controls** (no fake buttons). Live cancel/steer of an
    in-flight batch is future work. The existing single-run "Code" screen is unchanged.

## [0.29.0] - 2026-07-15

### Added
- **Desktop "Code" screen — three honest-by-construction upgrades.**
  - **Real verify output in the run receipt.** Each attempt now surfaces the verifier's actual captured
    stdout/stderr (the concrete test/assert output) in a collapsed panel — shown only when non-empty,
    never fabricated. It's the real command output the loop already recorded.
  - **Plan preview + optional plan injection ("plan mode").** A new `POST /api/plan` endpoint runs ONLY
    the planner — no edits, no tools, no agent loop — and returns the concrete steps. The UI shows them
    as a numbered checklist you can review and edit, then **Run with this plan** (the approved/edited plan
    is used verbatim, skipping re-planning) or **Run without a plan** (unchanged behaviour). The preview
    makes zero file changes; a model hiccup degrades to empty steps, never an error. Backed by a small
    core seam: `AutonomousAgent` accepts an injected `plan` that, when set, is used instead of calling the
    planner (covered by a test asserting the planner isn't invoked when a plan is provided).
  - **Model + mode (single / fuse / cascade) per run.** The run panel now takes an optional model slug and
    a routing toggle, reproducing `chimera solve --fuse` / `--cascade` wiring for the in-app trigger. The
    default (no model, single mode, no plan) run is byte-identical to before.

## [0.28.0] - 2026-07-15

### Added
- **Native desktop app (Tauri) — self-contained, opt-in.** A native window + system-tray shell that
  launches a PyInstaller-**frozen** copy of the SAME `chimera app` server as a sidecar, so the installer
  is **zero-dependency** (no system Python, no `pip install`). The shell picks a free port, the backend
  writes its URL to a file (`chimera app --emit-port-file`), and the window loads that localhost origin —
  the SPA is served same-origin so nothing in the frontend changed. Installers (`.exe`/NSIS, `.dmg`,
  `.AppImage`/`.deb`) are built by a new `desktop-release.yml` CI matrix and attached to each GitHub
  Release; they are **unsigned for now**, so first run shows a SmartScreen/Gatekeeper warning (called
  out in the release notes). The **terminal CLI stays fully sovereign** — this app is optional, and the
  lean PyPI wheel is unchanged (the 200 MB+ frozen sidecar lives only in the installers, never the wheel).
  Foundation proven locally on Windows (the frozen backend serves the real API + bundled UI); the Rust
  shell and the macOS/Linux installers are CI-verified.
- Benchmarks in the app + README: the honest weak-model lift (internal suite, not-yet-significant) and
  the external Terminal-Bench number, with n/CI/significance shown — no cherry-picking.
- **Pre-registered expansion of the internal weak-model-lift suite (6 → 15 tasks).** Rather than re-roll
  the same 6 tasks until they cross significance (p-hacking — the exact dishonesty this bench exists to
  avoid), the honest way to gain power is *more tasks*: 9 neutral tasks were registered before the run
  (each validated against a reference solution first) and the paired A/B was re-run on the goldilocks
  model. The lift **shrank as the suite grew** — from +50pp (n=6) to **60.0% → 73.3%, +13.3pp (n=15),
  95% CI [−4.2%, +13.3%], still not significant** — because the new tasks are ones the model one-shots.
  What holds across both runs: **zero regressions** and every point of lift is a task the loop *recovered*
  (raw fail → verified pass: `template_render`, `topo_sort`). The shipped snapshot, the app's Maturity
  panel, the README + all 6 translations now cite this n=15 number. (`bench/local_lift/RESULTS.md`.)

### Fixed
- `chimera app`: a busy bind port no longer crashes — it falls back to an OS-assigned free port (the
  socket is pre-bound and handed to uvicorn, so there's no rebind race). `--port 0` = any free port.
- Install friction on a fresh Python 3.11: a CI **install-matrix smoke** (ubuntu/windows/macos ×
  py3.11/3.13, clean `pip install .[desktop]` + import/CLI smoke) now guards that a wheel-only install
  works, plus a README "install trouble?" note — after diagnosing a transient litellm-transitive
  sdist/Rust build (not a Chimera defect; all cp311 wheels exist, proven by `--only-binary=:all:`).
- Benchmark runner: `run_paired.py` reconfigures stdout to UTF-8 so the paired report's `Δ` glyph no
  longer aborts the run (and drops `results/paired.json`) on a Windows cp1252 console.

## [0.27.0] - 2026-07-14

### Added
- Live per-edit diffs: during a coding run, the Code screen streams the real unified diff of each file
  the agent edits as it happens (via a new `on_edit`/`edit` event through the solve loop — the CLI
  event sink gets it too), alongside the final receipt diffs.
- Code screen: a git panel (status/diff/commit, explicit-path staging) and accept/discard for a run's
  changes (git-backed revert scoped to the run's files) — honest empty-state when the folder isn't a
  git repo.

## [0.26.0] - 2026-07-14

### Added
- **Code screen Phase 2: editable file viewer with save (guarded, newline-preserving, atomic) and a
  streaming command-runner (workspace-scoped, honors the sandbox setting, fresh subprocess per command
  — not an interactive terminal).** The viewer gains an opt-in **Edit** toggle → a mono editor →
  **Save**, which PUTs to a new `PUT /api/fs/file` (path-scoped by the same workspace guard, atomic
  temp+replace, preserving the file's own CRLF/LF newline, size-capped at 1 MB); truncated/binary
  files stay read-only (saving would clobber the unseen remainder), a dirty badge shows unsaved edits,
  Discard reverts, and the copy is honest that there's **no undo after save** (unless the folder is a
  git repo you commit). A new **Command runner** panel streams `POST /api/fs/exec` line by line
  (combined stdout+stderr) then the exit code: each command is a **fresh subprocess** — cwd/env don't
  persist between commands (no `cd`/`export` state) — run in your workspace on the host (or, when
  `CHIMERA_SANDBOX=docker`, one-shot inside the configured sandbox, kept isolated), reusing the shell
  tool's secret-scrubbed env, timeout cap (1..3600 s), and cwd guard. It is deliberately labeled a
  command-runner, not a terminal, and renders no fake interactive prompt/TTY. Both endpoints are
  localhost + bearer-guarded; a `..`/oversize/cwd-escape is a clean 400.
- **Code screen (Phase 1): a workspace file tree + viewer and a verify-or-revert coding runner — the
  agent edits your folder and the real per-file diff of what it changed (or attempted + reverted) is
  shown; run receipts now carry real unified diffs (benefits the CLI too).** Pick a workspace, browse
  a lazy (one-level-at-a-time) file tree, open a file in a syntax-highlighted read-only viewer
  (binary/truncated files are labeled honestly), then type an instruction + optional verify command
  and Run it: the agent runs the same plain solve core as `chimera solve` in a terminal — writing
  files and executing your verify command inside the workspace (localhost, bearer-guarded) — streamed
  live, and the newest run's per-file unified diffs are rendered (green `+` / red `-` / dim `@@`,
  each file collapsible). A **reverted** attempt's diffs are clearly labeled as what it ATTEMPTED
  before the workspace was rolled back after verification failed. New read-only `GET /api/fs/tree` and
  `GET /api/fs/file` endpoints are path-scoped by the same workspace guard the file tools use. The
  diff seam is bounded (≤20 files per attempt, each patch ≤4000 chars, flagged when truncated) and
  benefits the CLI: every autonomous run receipt now records the real unified diff of each attempt.

## [0.25.0] - 2026-07-14

### Added
- **MCP / Integrations: configure MCP servers (persisted to `.chimera/mcp.json` via `chimera mcp
  add/list/remove/test` or the app), live-test them, and opt into loading their tools
  (`CHIMERA_MCP_AUTOLOAD`).** A new terminal-first store makes MCP servers a real, persisted
  capability instead of a code change: the `chimera mcp` subcommands (add/list/remove, and `test`
  which does a REAL stdio connect and prints the tools it exposes) are the source of truth, and the
  desktop **MCP** screen is a view/editor over them (add a server, remove it, and a per-server Test
  whose green "connected · N tools" badge appears ONLY after a successful live connect — never by
  default). Config reads never connect and never return `env` secret values (only key names); the
  test endpoint (`POST /api/mcp/{name}/test`, `McpTestOut`) is the sole connecting one and flattens
  every failure to a short, secret-free error. Opt-in `CHIMERA_MCP_AUTOLOAD` (off by default) loads
  the configured servers' tools into the agent at app start — a broken server is skipped gracefully
  so it can never break boot; toggling it needs a restart, and MCP tool output stays untrusted
  (fenced + taint-tracked by governance).
- **First-run onboarding wizard: the desktop app now boots without a key and guides you to add +
  live-test an OpenRouter key from the UI (`chimera app` no longer hard-exits without a key).** When
  the doctor reports no provider key, the app opens a full-screen setup screen (the GUI equivalent of
  `chimera init`) instead of the chat: paste an OpenRouter key, Save it, then Test it. It stays
  honest about "present vs verified" — after Save the key is only *saved (present)*; it says
  *verified — it works* ONLY after a real 1-token call passes, via the one new endpoint
  `POST /api/config/test` (`ConfigTestOut`, the sole place that authenticates a key; presence checks
  never do). A wizard-set key is usable the same session (PATCH now also updates the live process
  env), and a keyed user never sees the wizard. Optional default-model + cost-mode selects and a
  "skip to Settings" link are included; the CLI stays fully sovereign (only `chimera app` relaxes —
  run/solve/fuse still require a key).
- **Tools screen: lists the agent's registered tools with capability tags (network/read/write/exec/side-effect).**
- **Maturity screen: the agent's coverage scorecard by surface (live from the test suite, or a shipped snapshot), with the weakest surface highlighted.**

## [0.24.0] - 2026-07-14

### Added
- **Memory screen: layers (by kind) + provenance (clean/unverified) + by-source breakdown.** A new
  layers panel above the fact list shows total/clean/unverified tiles, per-kind counts (the full
  `working · episodic · semantic · persona` taxonomy, 0-count kinds included) with a clean/unverified
  split, and a by-source list, served by a guarded `GET /api/memory/layers` (`MemoryLayersOut`). It
  reports only per-kind fact counts (never a vector-index count) and adds an honest note when the
  opt-in semantic-embeddings layer is off.
- **Governance / Security screen in the desktop app.** A new sidebar view shows the injection
  red-team scoreboard (attack-success-rate with vs. without the defenses, per-category and per-attack,
  naming the honest gap that still gets through even defended) plus an audit-log viewer, served by
  guarded `GET /api/governance/injection` (`InjectionReportOut`) and `GET /api/governance/audit`
  (`GovernanceAuditOut`). The injection corpus is synthetic and needs no model; the audit log is
  empty by default (only CLI guarded/tainted runs write it), and the UI says so honestly.
- **Runs screen in the desktop app.** A new sidebar view shows how each autonomous run
  (`chimera solve`) PROVED its work: per run, the task, a pass/fail/paused status badge, the verify
  command that judged it, and the verify-or-revert attempt trail — each attempt's index, a
  verified ✓/✗, a `reverted` (↩) flag, the workspace diff it actually made (audited before any
  revert), and the concrete verifier output in a collapsible. Shows only real captured evidence —
  an empty field is omitted, never fabricated. Each finished run is persisted best-effort to an
  append-only `runs.jsonl` (persisting a receipt can never break a run) and served by a guarded
  `GET /api/runs` endpoint (`RunReceiptOut`, most recent first, last 100).
- **In-app run trigger (streaming).** The Runs screen now has a **New run** panel that launches an
  autonomous run from the desktop — a task, an optional shell verify command (exit 0 = pass), an
  optional workspace, and a max-attempts count — over a new guarded, streaming `POST /api/runs`
  (`RunRequest` → SSE `event`/`done`/`error`, mirroring the chat stream). Live progress (planning,
  per-attempt, verifying, terminal pass/fail) streams in as it happens, and the run's receipt (step
  3a) persists itself via the agent's `run_log`, so the finished run appears in the list below. The
  trigger runs the PLAIN solve core (plan → run → verify-or-revert → receipt) — the advanced CLI
  seams (cascade, taint, evolution, durable threads, strong-verify, contracts) are intentionally
  omitted. **Safety posture:** it writes files and executes the user-supplied verify command in the
  workspace — the same capability the chat endpoint's file/shell tools already have, and the same as
  running `chimera solve` in a terminal — kept behind the bearer guard and the localhost bind, never
  running outside the resolved workspace. The UI copy states this plainly.
- **Cost / Usage dashboard in the desktop app.** A new sidebar view aggregates every chat turn's
  real token/cost accounting: totals (turns · tokens · spend), a hand-rolled SVG spend-per-day chart
  (falls back to tokens/day when prices are unknown), a per-model ranked breakdown, top sessions, and
  cache-hit % + route-mix (single/fusion/cascade) tiles. Honest about cost: a turn whose price is
  unknown (`usd=None`) is counted as **unpriced**, never summed as $0 — the total only adds prices it
  actually knows, and no fabricated "router savings" number is shown. Each turn is persisted to an
  append-only `usage.jsonl` (best-effort — usage logging can never break a turn) and served by a new
  guarded `GET /api/usage` endpoint (`UsageSummaryOut`), with the model slug now threaded from
  `AgentResult` → `TurnReport` → the log for the per-model view.
- **Fusion & Cascade screen in the desktop app.** A new sidebar view shows, for the latest turn, HOW
  the answer was composed: the fusion panel → judge → synthesis breakdown (with per-model tokens,
  panel diversity, and aggregation mode), or the cascade tier ladder (weak → mid → fusion) with the
  accepted tier highlighted. Shows only real per-turn data — a single-model turn gets an honest empty
  state. Backed by a new neutral `route_meta` JSON seam threaded from the fusion/cascade backends
  (`CompletionResult.route_meta`) through the agent, session, and API up to the SSE `done` payload (no
  OpenAPI change — the trace rides the hand-typed SSE event). The desktop `chimera app` backend now
  also honors the Settings **Cascade** toggle.
- **"Fuse this turn" toggle in the composer.** Because the chat agent always carries tools and both
  the routed and cascade backends send tool-turns to a single model, fusion never triggered in normal
  chat. The composer now has a **Fuse** toggle: with it on, that turn is routed through the fusion
  engine tool-free (panel → judge → synthesizer), so the Fusion screen shows the real breakdown.
  Verified live: a fused turn produced a 3-model panel (deepseek + gpt-4o-mini + llama-3.3-70b), a
  judge analysis, and a synthesis — all rendered on the Fusion screen.

## [0.23.0] - 2026-07-13

**The desktop app speaks 7 languages.** A language selector (the same 7 languages as the README) with
browser auto-detection.

### Added
- **Interface language selector — 7 languages (en / pt / es / fr / de / zh / ja).** A new
  **Settings → Appearance → Language** control. On first run the UI auto-detects the browser language
  and falls back to English; the choice persists to `localStorage`. Every visible string across Chat,
  Sessions, Memory, Skills, Schedule, Tasks, Settings, and the activity panel is translated through a
  small in-app dictionary (`src/lib/i18n.tsx`) — no i18n runtime dependency added (~27 kB of strings).

### Fixed
- **PWA icon is no longer declared `maskable`.** The brand icon has a wordmark near its lower edge that
  maskable launchers (e.g. Android adaptive icons) would crop, so the manifest now declares only
  `purpose: "any"`.

## [0.22.1] - 2026-07-13

### Changed
- **Real Chimera brand icon in the desktop app.** Replaced the placeholder `🔺` emoji mark (rail,
  sidebar header, chat empty-state, assistant avatar) with the actual icon (blue lion + cyan serpent
  on deep navy), served from `/chimera-icon.png` via a shared `BrandMark` component, and pointed the
  favicon + PWA manifest at it too (dropping the placeholder `icon.svg`).

## [0.22.0] - 2026-07-13

**The desktop app now ships in the wheel — and got a full neumorphic redesign.** Two things landed
together: the built UI is bundled into the PyPI package for the first time (so `pip install
chimera-agent[desktop]` actually gives you the app, not just the API), and every screen was
redesigned into a soft-UI system harmonized with the Chimera brand.

### Fixed
- **The desktop UI is now bundled in the wheel (it never was before).** The CLI resolved the built
  SPA at `apps/desktop/dist`, a *source-checkout* path, so a `pip install chimera-agent[desktop]` user
  only ever got the API with a "UI not built" note — the app announced since v0.20.0 was effectively
  source-only. The wheel now force-includes the built SPA as `chimera/_desktop_dist` (via
  `artifacts` + `force-include` in `pyproject.toml`), and `chimera app` / `serve --ui` prefers a
  source-checkout `dist` (for live dev rebuilds) and falls back to the packaged copy. Verified against
  the built wheel: `chimera/_desktop_dist/{index.html,assets,manifest.webmanifest,sw.js}` are present
  and the installed-layout resolver picks them up.

### Changed
- **Neumorphic soft-UI redesign of the whole desktop app, harmonized with the brand.** A dark
  deep-navy ground with a subtle radial accent glow; raised "surface" cards (soft outer shadow + top
  highlight); recessed inset fields with a blue focus glow; blue→cyan gradient primary buttons and
  pill CTAs; glowing gradient toggles; and accent-haloed active icons in the left rail — all driven by
  CSS-variable shadow tokens that adapt per theme (a clean flat light fallback is kept). The palette
  (blue + cyan on navy) is taken from the Chimera icon. Frontend-only: no API or contract change, so
  the generated OpenAPI types are untouched. Verified live across Chat, Settings, Memory, Skills, and
  Tasks.

## [0.21.2] - 2026-07-13

**Desktop API hardening + a drift-proof typed frontend client.** An adversarial review of the new
`chimera/api` surface fixed 8 bugs (2 security-relevant), and the frontend's types are now generated
from the backend's OpenAPI schema so the two can't drift.

### Fixed
- **Desktop API hardening — 8 fixes from the 17th adversarial review (2 security-relevant).** (1) The
  `.env` config write allowlisted the key but not the value, so a newline in a value could inject
  extra `.env` lines (e.g. a provider key) — values with `\r`/`\n` are now rejected. (2) When a server
  token is set, the **read** endpoints (transcripts, memory, config) are now guarded too, not just
  mutations; the token is injected into the SPA **only for a loopback client** so the local browser
  still authenticates while a remotely-exposed instance never hands the secret to remote clients.
  (3) The token guard now reads settings fresh, so a token set at runtime via the UI takes effect
  immediately. (4) Concurrent turns on the same session serialize behind a per-session lock instead of
  racing the non-thread-safe `ChatSession`. (5) The live-session cache is LRU-bounded (no unbounded
  growth from random session ids). (6) `memory` search `k` is clamped (a negative `k` can't dump the
  whole store). (7) An unknown `/api/*` path returns 404 instead of the SPA's index. (8) Documented
  that an in-flight turn's token cost isn't reclaimed if the client disconnects mid-stream. The SSE
  bridge auth, secret masking, session/SPA path-traversal guards, and project HITL (no token spend on
  approve/deny) reviewed clean.

### Added
- **Desktop API is fully typed; the frontend generates its types from it (no contract drift).** Every
  desktop endpoint now declares a Pydantic `response_model` (`chimera/api/schemas.py`), so
  `/api/openapi.json` describes exact response shapes. The frontend's API types are **generated** from
  that schema (`python -m chimera.api.schema_dump` → `npm run gen:api` → `src/lib/api-schema.ts`) and
  re-exported from `types.ts`, so a backend model change surfaces as a TypeScript build error instead
  of a silent runtime mismatch. (The SSE chat-stream payloads stay hand-written — they aren't typed
  HTTP bodies.)

## [0.21.1] - 2026-07-12

### Added
- **Desktop app is installable (PWA, M21 Fase D).** The built UI is now a Progressive Web App — with
  `chimera app` running, Chrome/Edge offer **Install**, and it opens in its own window with a
  taskbar/dock icon, no Electron or Tauri. A minimal service worker caches the static shell for
  instant startup and deliberately **never intercepts `/api`**, so the SSE chat stream is untouched.
  (Added a manifest, an SVG app icon, the service worker + registration, and the correct
  `.webmanifest` MIME type on the backend.) Verified live: SW registered/active at scope `/`,
  manifest valid (standalone display).

## [0.21.0] - 2026-07-12

**Chimera Desktop grows a full app — Settings + Memory / Skills / Schedule / Tasks.** The desktop UI
(from v0.20.0) is now a complete control surface: a Settings screen (models / API keys / cache /
sandbox / server token, secrets always masked) and a left icon-rail navigating dedicated Memory,
Skills, Schedule, and Tasks screens — every one a view over the real managers, with the project
milestone **approve/deny (HITL)** wired in. All read/HITL endpoints are LLM-free; token-spending
paths stay on chat/solve.

### Added
- **Desktop app — Memory / Skills / Schedule / Tasks screens + feature API (M21 Fase C).** New
  endpoints reuse the existing managers (no reimplementation, no reimplemented state): memory
  (`GET/POST/DELETE /api/memory` + `/api/memory/profile`), skills (`GET /api/skills` +
  approve/retire), cron (`GET /api/cron` + enable/disable/delete), and tasks (`GET /api/kanban`,
  `GET /api/projects[/{id}]` + **HITL** `approve`/`deny` on milestone checkpoints). The
  token-spending paths (running a project step, executing a skill) are deliberately kept off these
  endpoints — the app drives those through chat/solve. The UI gains a left icon-rail navigating to
  Chat / Memory (browse/search/add facts, persona flag, taint badges) / Skills (learned skills with
  status + uses/wins + approve/retire) / Schedule (cron jobs with enable toggle) / Tasks (projects
  with approve/deny for high-risk steps + the kanban board) / Settings.
- **Desktop app — Settings screen + config API (M21 Fase B).** New `GET /api/config`, `PATCH
  /api/config`, and `GET /api/doctor` endpoints back a Settings screen (Model / API keys / Memory /
  Cache / Sandbox / Server token) mirroring the reference apps. **Secrets are never returned in
  cleartext** — each key reports `{set, hint}` with at most a last-4 hint (the server token reports
  presence only); writes go to `.env` through a strict allowlist (no arbitrary-key injection) and
  clear the settings cache. The write endpoint requires the bearer token when one is configured.

## [0.20.0] - 2026-07-12

**Chimera Desktop — a local web app (React ↔ Python over HTTP+SSE).** A new opt-in `[desktop]` extra
adds a FastAPI HTTP+SSE API and a Vite/React/Tailwind UI that `chimera app` serves same-origin. The
chat streams tokens live and shows the real tools / cost / memory each turn used; conversations
persist. Built on the existing agent stack — no new engine, and the core CLI is untouched.

### Added
- **Desktop app backend (M21 Fase A).** A new opt-in `[desktop]` extra (`pip install
  chimera-agent[desktop]`) adds a FastAPI HTTP+SSE API (`chimera/api/`) that the forthcoming React UI
  (`apps/desktop`) consumes, plus a `chimera app` command that serves it (and the built SPA
  same-origin) and opens the browser. The flagship `POST /api/chat/stream` streams `token`/`tool`/
  `done` Server-Sent Events by bridging the existing `ChatSession.send_verbose` callbacks — reusing
  the real agent stack unchanged; under `--fuse` it degrades to a final-answer event (no fake cursor).
  Conversations now **persist** to `<home>/sessions/<id>.json` (atomic writes, corrupt-file tolerant),
  exposed via `GET/POST/DELETE /api/sessions`. The bearer token (`CHIMERA_SERVER_TOKEN`) guards the
  mutating endpoints when set; the core CLI and stdlib messaging gateway are untouched.
- **Desktop app frontend (`apps/desktop`).** A Vite + React + TypeScript + Tailwind + shadcn-style UI
  that `chimera app` serves same-origin. Fase A ships the flagship **Chat** screen (three panes — a
  Markdown/code transcript, a live token buffer, and an activity sidebar showing the real tools /
  tokens / cost / memory each turn used) and a **persisted Sessions** list. Talks to the backend over
  HTTP+SSE (POST stream read via `fetch`, since SSE lives on a POST). Built and smoke-tested end to
  end against a real model. Settings / Memory / Skills / Cron / Tasks screens are Fase B/C.

## [0.19.8] - 2026-07-12

**Provider-gateway hardening — 4 fixes from the 16th adversarial review (1 HIGH).**

### Fixed
- **The completion cache no longer defeats consensus checks.** It served `temperature>0` samples, so
  `k` identical sampled requests returned byte-identical content — collapsing cascade agreement, the
  fusion router's agreement gate, and self-consistency into **fake unanimity** (false confidence).
  The cache now serves/stores **only deterministic (`temperature==0`) requests**.
- **An empty-content result is no longer cached** (one malformed response would otherwise serve `""`
  forever at $0 for that key).
- **`acomplete()` now honors the credential pool** — a pool-only config (`CHIMERA_OPENROUTER_KEYS`
  with no single `*_API_KEY`) previously AUTH-failed; its docstring is corrected (it is not used by
  the fusion panel, which runs threaded `complete()`).
- **The lazy key-rotator creation is lock-guarded** against the fusion panel's threads (two threads
  racing the first call for a provider could build two rotators and drop an advanced index).

## [0.19.7] - 2026-07-12

**Scheduler & migration hardening — 8 fixes from the 15th adversarial review (2 security-relevant).**

### Fixed
- **Scheduler — a corrupt `jobs.json` no longer wipes every cron.** A truncated or typo'd store file
  used to crash the whole store on load *and* clear all in-memory crons (the JSON parse sat outside
  the per-entry guard, and `_jobs` was cleared before the parse). The store now keeps its current
  jobs on an unreadable/invalid file and leaves its mtime unchanged, so a later fix reloads cleanly.
- **Scheduler — concurrent `cron add` is no longer lost.** A long-lived daemon holding a stale
  snapshot could clobber a job another process added while it was dispatching a long task. Writes now
  fold in unknown on-disk jobs first (best-effort; not a substitute for an OS lock, and documented as
  such).
- **Scheduler — atomic writes use a unique temp file.** Two concurrent writers previously shared a
  fixed `jobs.json.tmp` and could corrupt each other's write; each write now uses a unique temp name.
- **Scheduler — webhook jobs registered after `chimera serve` starts now fire** (the handler reloads
  the store before dispatch instead of holding a frozen copy).
- **Migration — `migrate` can no longer overwrite arbitrary files via a symlinked skill.** A hostile
  skill dir with a `SKILL.md` symlinked to e.g. `~/.bashrc` could be written through during
  taint-stamping; the taint pass now skips symlinks and requires the real path to stay under the
  skills dir.
- **Migration — a dotted skill filename (`planner.v2.md`) keeps its extension and is taint-stamped**
  (it used to be copied without `.md` and skip the taint boundary); files are now keyed by full name,
  which also fixes a dir/file same-stem collision.
- **Migration — `migrate <source> <bad-path>` now exits non-zero** instead of silently reporting
  success on a nonexistent source directory.

## [0.19.6] - 2026-07-12

**Cheaper skill-card injection.** The self-evolution flywheel's card-reading loop was kept opt-in
mostly because injecting cards cost +~349% tokens on short tasks. This release cuts that ~7–9× — the
honest engineering lever behind the M19-A1 default flip (which still waits on a larger accuracy A/B,
not on cost).

### Changed
- **Skill-card injection is ~7–9× cheaper (M19-A1 cost reduction).** Injecting learned reasoning cards
  used to cost +~349% tokens on short tasks — the decisive blocker keeping the flywheel's card-reading
  loop opt-in. Three levers cut it to ~+37–48% (measured, two runs, now under the +50% gate): inject
  the **single** best card (`CHIMERA_SKILL_CARDS_K=1`, was 3), a **relevance gate**
  (`CHIMERA_SKILL_CARDS_MIN_OVERLAP=2` — a task with no strong match injects **nothing**, so it pays no
  tokens and gets no misleading card), and a **shorter render** (`CHIMERA_SKILL_CARDS_MAX_LINES=3`).
  These are the new defaults (they only apply when card reading is opted into). **The default still
  stays OFF**: the cost blocker is removed, but the accuracy lift isn't statistically significant at
  n=12 (it swung +16.7pp → −8.3pp between identical free-model runs) — flipping it now waits on a
  larger paired A/B, not on code. See `bench/skillcard/RESULTS.md`.

## [0.19.5] - 2026-07-12

**CLI honesty + safe prune.** A fourteenth adversarial review, a thorough 2nd pass of the ~4500-line
Typer CLI. Most command groups reviewed clean; the fixes make CI-facing gates exit non-zero on
failure and stop `memory prune` from silently deleting profile data.

### Fixed
- **CLI gates now exit non-zero on failure.** `chimera workflow` and `chimera lifecycle` (and the
  `fusion-bench`/`cascade-bench`/`skillcard-bench` verdicts) printed a red "failed"/"REGRESSION" but
  exited 0 — so `chimera workflow triage.yaml && ./deploy.sh` would deploy on a failed workflow, and a
  bench regression stayed green in CI. They now `raise typer.Exit(1)` on the failing branch, matching
  `solve`/`drift`/`transfer-gate`. A fourteenth adversarial review, of the CLI.
- **`memory prune` no longer silently deletes data.** It ran immediately and hard-deleted everything
  below the budget — including `persona`/profile facts. It is now **dry-run by default** (shows the
  count; `--apply` to delete), and **persona facts are never pruned** (the budget applies only to
  prunable items), matching the reversible, `--apply`-gated `skills-retire`.
- **`solve` HITL flags are validated.** Passing more than one of `--approve`/`--respond`/`--edit`/
  `--deny` is refused (they could pick one thread but run another's action), and `--edit` now requires
  `--answer` (it would otherwise finalize the paused run with an empty answer).
- **`.env` writes are atomic** (temp + `os.replace`) so a crash mid-write can't truncate the user's
  secrets/config.

## [0.19.4] - 2026-07-12

**Crew resilience.** A thirteenth adversarial review, of the multi-agent roles/crew surface. Most of
it reviewed clean; the three fixes keep a crew honest and crash-proof — including a **crash when a
single reviewer hit a transient error**.

### Fixed
- **A crew's parallel review no longer crashes when one reviewer fails.** `parallel_review` /
  `SupervisorCrew` used `Executor.map`, which re-raises the first worker's exception — so one worker
  hitting a transient provider error sank the whole `chimera crew --mode supervisor` run and discarded
  every other reviewer's completed work. Each reviewer now degrades to an `[error]` message instead,
  so the panel survives N-1 workers (a thirteenth adversarial review, of the roles/crew surface).
- **The supervisor now sees the real consensus.** Reviews were consolidated (near-duplicates merged)
  *before* the supervisor synthesized them, collapsing a 3-to-1 majority into a 1-to-1 tie; the
  supervisor now receives the raw reviews (dedup is kept only for memory storage).
- **Per-role tool allowlist is now enforced, not decorative.** A `Role.allowed_tools` filters the
  registry fail-closed before the worker's agent loop, so a role can't reach a tool outside its remit
  even when it shares the crew's registry.

## [0.19.3] - 2026-07-12

**Self-evolution flywheel hardening.** A twelfth adversarial review, of the evolution internals. Its
statistical core (the honest-benchmark gates: McNemar/Wilson/Newcombe, transfer-gate, diff-gate,
rollback, GEPA) reviewed clean; the six fixes are in the skill-store persistence and lifecycle
plumbing around it — including a **crash could lose the entire learned-skill library**.

### Fixed
- **The learned-skill store is now crash-safe (data integrity of the differentiator).** `SkillStore`
  wrote `skills.json` with a plain truncating write on every `record_use` (the flywheel's
  highest-frequency write) — a crash mid-write left it unloadable, losing the whole learned-skill
  library. It now writes atomically (temp + `os.replace`) and skips a single malformed entry (or a
  corrupt file) on load instead of aborting, matching the convention every sibling store already
  follows. A twelfth adversarial review, of the self-evolution internals — whose statistical core
  (McNemar/Wilson/Newcombe, transfer-gate, diff-gate, rollback, GEPA) reviewed clean.
- **A regressed skill can now actually be demoted.** Promotion carried the lifetime win-rate forward,
  so an `active` skill that started strong then broke needed ~8 consecutive failures to cross the
  retire threshold; `promote` now resets the probation counters so demotion tracks *recent* behavior.
- **Deterministic skill retrieval.** The FTS ranking now tie-breaks on name, so which card lands in
  the top-k (and gets credited a use/success) can't flip between runs on equal BM25 rank.
- **No leaked SQLite connection per retrieval** — the in-memory card index is now a closing context
  manager — and a resumed HITL-approved run no longer credits skill cards it didn't actually use.

## [0.19.2] - 2026-07-12

**File-edit data integrity.** An eleventh adversarial review, of the file-mutation surface
(`edit_file`/`apply_patch`/`write_file`), found that edits silently rewrote every line ending and
weren't crash-atomic — the logic guards were solid, but the byte-I/O layer wasn't. All fixed and
byte-level regression-tested.

### Fixed
- **File edits no longer silently rewrite every line ending (data integrity).** `edit_file`,
  `apply_patch`, and `write_file` read/wrote via `Path.read_text`/`write_text`, whose universal-newline
  translation flipped the WHOLE file to the platform's line ending (a one-line edit on an LF file
  became a whole-file CRLF diff on Windows, and vice-versa on Linux). They now read as bytes normalized
  to `\n` for matching and write back preserving the file's **original** newline convention — an
  eleventh adversarial review, of the file-mutation surface. A CRLF file also now anchors a model's
  `\n`-based match string correctly.
- **File writes are crash-atomic.** `edit_file`/`apply_patch`/`write_file` write to a temp file and
  `os.replace` it into place, so a crash or I/O error mid-write can't truncate the user's existing file
  (the documented "atomic" guarantee now holds against I/O failure, not just logical anchor failure).
- **`apply_patch` anchors every hunk against the ORIGINAL content** (not a copy mutated by earlier
  hunks) and rejects overlapping target regions — a later hunk can no longer accidentally match text an
  earlier hunk inserted.
- **`edit_file`/`apply_patch` on a non-UTF-8 file** return a clear "not a UTF-8 text file" error
  instead of an opaque generic failure (the file is never touched).

## [0.19.1] - 2026-07-12

**Streaming-TUI hardening + memory-layer label.** A tenth adversarial review, aimed at the brand-new
v0.19.0 streaming surface, found four gaps (no CRITICAL/HIGH — the concurrency race was the only MED);
the activity panel also learned to name which memory layer contributed. All fixed and
regression-tested.

### Fixed
- **TUI: a second Enter can't start a concurrent turn.** The input is disabled while a turn is in
  flight (a thread worker can't be preempted, so a second submit would run a concurrent `send_verbose`
  on the same non-thread-safe `ChatSession`, interleaving the transcript and live buffer); it re-opens
  when the turn finishes. Found in a tenth adversarial review of the new streaming surface.
- **`stream_complete` degrades instead of erroring on strict providers.** `stream_options` is now sent
  with `drop_params=True`, so a native anthropic/gemini model (or a strict `api_base`) that rejects the
  param drops it and streams anyway (usage just comes back unknown) rather than failing every TUI turn.
- **Streamed tool calls without a provider `index` no longer split in two.** Fragments now merge into
  the currently-open call instead of minting a new slot per chunk (which lost the arguments).
- **Cost honesty:** the activity panel flags "(excl. cache)" next to the turn cost when cache tokens
  are present, since the price is off prompt+completion at list rate and cache reads/writes bill
  differently.

### Added
- **The TUI activity panel now names the memory layer that contributed.** `MemoryManager.search`
  gained an optional `on_layer` callback that fires with the layer producing the hits
  (`semantic`/`fts`/`keyword`, never when a layer returns nothing); `ChatSession` surfaces it on the
  `TurnReport`, so the panel shows e.g. "3 facts recalled (keyword+graph)" instead of just a count —
  the honest label, absent rather than guessed when unknown.

## [0.19.0] - 2026-07-12

**A live, instrumented terminal UI.** `chimera tui` goes from a single-pane chat to a streaming
workspace: the model's tokens appear as they're generated, replies render as syntax-highlighted
Markdown, and a side panel shows what the agent actually did (tools, tokens, cost, memory) — with
strict honesty about what a chat turn can and can't report.

### Added
- **The TUI is now a live, instrumented workspace.** `chimera tui` gained three things: (1) **real
  token streaming** — the model's text streams into the log as it arrives (single-model path), and
  finished replies render as **Markdown with syntax-highlighted code**; (2) an **activity panel** that
  shows, from real signals, the tools the agent called this turn, the token count + cost, and how many
  memory facts were recalled; (3) **ergonomics** — a command palette (`Ctrl+P`), slash-command
  autocomplete, more key bindings (`Ctrl+R` reset, `Ctrl+L` clear, `PgUp`/`PgDn` scroll), and a
  graceful fallback to `chimera chat` if Textual is missing. Honest by construction: no token stream
  under `--fuse` (the panel says "synthesizing"), cost reads "unavailable" when unpriced, and there is
  no verify/revert indicator in chat (that runs only in `solve`/`project`).
- **`LLMGateway.stream_complete(...)`** — a streaming completion that pushes text deltas to an
  `on_delta` callback while reassembling the full content, tool-call deltas (by index), and final
  usage into a normal `CompletionResult`. `Agent.run` now accepts `on_token`/`on_tool` callbacks and
  aggregates per-run token usage + cost into `AgentResult`; `ChatSession.send_verbose()` returns a
  `TurnReport` with the turn's activity. All additive — the blocking `complete`/`send` paths are
  unchanged.

## [0.18.8] - 2026-07-12

**Project orchestrator hardening + a verify-or-revert demo.** A ninth adversarial review (the
`chimera project` loop + kanban DAG) found seven bugs — two of which could produce a false-green
completion or defeat the runaway rail. All fixed and regression-tested; the core "the spec is the
only authority of done" invariant held. Ships with a new example that demonstrates the honest
artifact: a *failed* change being detected and rolled back with a receipt.

### Added
- **`examples/revert_demo/` — verify-or-revert, demonstrated.** A deterministic, model-free example
  that copies a sample workspace, injects a regression, runs the acceptance check (which fails),
  reverts to the checkpoint, and re-checks (which passes) — printing a receipt at each step. It drives
  the real primitives behind `chimera solve`/`chimera project` (`WorkspaceGuard` + `CommandVerifier`)
  and exits non-zero if verify-or-revert ever failed to protect the tree.

### Fixed
- **`max_iterations` is now durable across a resume.** `project run`/`step`/`approve` rebuilt the
  config with the default `max_iterations=20`, silently overriding a `--max-iterations N` set at
  `start` — so a resumed project could run far past the cap the user set (the runaway rail's whole
  point). The rail (and `require_plan_approval`) are now persisted in `project.json` and rebuilt from
  disk on resume.
- **A spec with no requirements is refused instead of vacuously completing.** `check_drift` starts
  `aligned=True` and only a failing *required* requirement flips it false, so an empty or all-optional
  `requirements:` block (e.g. a YAML typo that parses to `[]`) reported "done" having verified
  nothing. `project start` now rejects it.
- **`project.json` is written atomically** (temp + replace, mirroring the board), so a crash
  mid-write can't truncate it and brick every later `status`/`run`/`resume` for that project.
- **A card interrupted mid-work is recovered, not orphaned.** A card left in `doing` by a crash was
  never retried (it counts as an open card so no replacement is made, but the picker only scans
  `backlog`) → a permanent "no ready card" escalation. Stale `doing` cards are now returned to
  `backlog` at the start of each step.
- **`deny_card` no longer raises if its card is gone from the board** (the pending id lives in
  `project.json`, not the board); it escalates cleanly, matching `approve_card`'s guard.
- **A required requirement that depends on an unsatisfied optional one now progresses.** The optional
  dependency previously never got a card, so the required requirement stayed blocked forever; the
  gap computation now pulls in the (transitive) unsatisfied dependencies of required requirements.

## [0.18.7] - 2026-07-12

**Skills, memory-graph & tools hardening.** An eighth adversarial review (skills/skill_md, memory
graph, native tools) found eleven real bugs — three of them **SSRF** holes in model-callable fetch
tools (`http_get` was already patched; `browser` navigate and `download_media` were not), plus a
workspace escape in `glob`, an unfenced document reader, and a status-laundering skill import — all
fixed and regression-tested. Eighth review in the series; 71 bugs found across eleven surfaces so far.

### Security
- **SSRF guard on `browser` and `download_media`.** Both take a model-/content-supplied URL and fetch
  it (Playwright `page.goto`, yt-dlp), with no host validation — so the agent could be steered into
  `http://169.254.169.254/…` (cloud metadata) or an internal service. Both now run the same
  `check_url` guard (`http_get` already did): every navigate/download target is rejected unless it is
  a public http/https host.
- **`glob` can no longer escape the workspace.** A pattern like `../../etc/passwd` (pathlib returns
  the escaping path) or one crossing a symlink leaked files outside the workspace root. Each match is
  now resolved and dropped unless it stays under the workspace.
- **`read_document` output is data-fenced.** A PDF/DOCX/HTML can carry a prompt injection just like a
  web page; its extracted text was returned raw. It is now defanged (control tokens) and wrapped in
  the data-fence markers, matching the browser/fetch tools.
- **Skill import no longer launders a probationary status.** `to_learned` collapsed a `provisional`
  (on-probation) imported skill to `active`, and an unknown/mistyped status also became `active`. The
  real status now round-trips, and any unknown status defaults to `pending` — never silent promotion
  into retrieval.

### Fixed
- **Graph recall matches whole words, not substrings.** A 1–2 char entity (`Go`, `AI`, `C`) matched
  inside unrelated words (`good`, `brainstorm`), polluting `related_facts`. Recall now requires a
  whole-word match.
- **Malformed SKILL.md frontmatter no longer crashes the parser** (untrusted import): broken YAML is
  treated as a body-only skill instead of raising.
- **Memory graph survives a corrupt/partial file.** `save` is now atomic (temp + replace) so a crash
  mid-write can't truncate it; `load` returns an empty graph on invalid JSON; `from_dict` skips a
  malformed relation instead of aborting the whole load.
- **`memory graph` builds only from clean memories.** Both the recall path and the `memory graph`
  command now exclude tainted facts from the entity-relation graph.

## [0.18.6] - 2026-07-12

**Migration, provider & sandbox hardening.** Two adversarial reviews (migration/kanban/CLI;
providers/sandbox/scrape) found fifteen real bugs the tests missed — including a **CRITICAL**
taint-laundering in the migration importer, an **SSRF** hole in the scrape tools, and a
provider-secret leak from the default sandbox — all fixed and regression-tested. Seventh review in the
series; 60 bugs found across ten surfaces so far.

### Security
- **Migration no longer launders foreign content as trusted (CRITICAL).** Imported memory was stored
  as `provenance="clean"` and imported skills were copied verbatim — so another agent's (untrusted)
  facts recalled as verified, and a foreign skill with `provenance: clean` was admitted `active` by
  `skills-import`, bypassing the "imported = pending until a human approves" gate. Imported memory is
  now tainted, and each imported SKILL.md is taint-stamped at the import boundary.
- **SSRF guard on the scrape tools.** `scrape`/`extract`/`map`/`crawl`/firecrawl had no host
  validation, so the agent could be steered into `http://169.254.169.254/…` (cloud metadata) or an
  internal service. Every fetch (and each redirect hop — redirects are now followed manually) is
  checked against private/loopback/link-local/reserved ranges; only http/https schemes are allowed.
- **LocalSandbox (the default) no longer inherits provider secrets.** The gateway exports API keys to
  `os.environ`, which the default sandbox passed wholesale to every command — an injected `echo
  $OPENROUTER_API_KEY` could exfiltrate them. Secret-looking env vars are now scrubbed from the child
  env (matching the Docker path).
- **Docker sandbox timeout actually stops the container.** A `docker run` timeout only killed the
  client; the container kept executing on the daemon. It now runs `--name`d and is `docker kill`ed on
  timeout.
- **Import files can't abort the whole import / pull host files via symlink.** Non-UTF-8 config/memory
  files degrade with `errors="replace"` instead of crashing mid-import; skill copies preserve symlinks
  (`symlinks=True`) instead of dereferencing them into the home.

### Fixed
- **LocalSandbox timeout kills the whole process tree** (`start_new_session` + `killpg`), so a forked
  grandchild can't survive the timeout or hang the reap.
- **Completion cache correctness & honesty.** The cache key now folds in the other response-affecting
  params (top_p / seed / stop / response_format / api_base) so different requests can't collide; a
  cache HIT reports 0 fresh tokens (with the count under `cache_read_tokens`) instead of re-billing a
  $0 call; a fallback model's answer is no longer cached under the primary's key; and the temp file
  is uniquely named so concurrent gateways can't corrupt it.
- **Unbounded fetch bodies are capped** (10 MB, streamed) so a huge response can't OOM the host.
- **`chimera migrate --apply` honors the configured memory backend** (it was writing to `memory.json`
  even on a sqlite home — a silent no-op) and the embedder.
- **Kanban board is crash-safe** (atomic save) and **resilient** (skips a malformed card instead of
  aborting the whole board load).

## [0.18.5] - 2026-07-12

**Data-integrity & server-security hardening.** Two adversarial reviews (memory-store + TUI; server/API
+ ecosystem) found sixteen real bugs the tests missed — including a **CRITICAL** taint-laundering in the
SQLite memory backend — all fixed and regression-tested, plus opt-in auth for the HTTP server. Sixth
review in the series; 45 bugs found across eight surfaces so far.

### Security
- **SQLite memory backend no longer launders taint (CRITICAL).** The optional SQLite/FTS5 store had
  no `provenance` column, so a `provenance="tainted"` memory came back `"clean"` on every read — the
  recall taint-label, and the remember/merge/consolidate anti-laundering guarantees, silently broke
  purely by choosing that backend. Provenance is now a first-class column (with a safe migration for
  existing stores that rebuilds the FTS5 table / ALTERs a plain table; old rows default to `clean`).
- **Entity-graph facts now pass the injection gate.** Recall reachable via the memory graph (not the
  keyword-similarity path) skipped `MemoryGate`, so a graph-linked tainted fact could inject override
  text into the prompt. Graph facts now go through an injection-only check (`gate.is_clean`).
- **HTTP handlers no longer leak internal exception text** (paths, config keys, provider bodies) to
  callers — a generic message is returned and the detail logged server-side. Webhook/chat routes are
  matched against the parsed path (so `?query` strings don't break them), and a malformed
  `Content-Length` returns 400 instead of dropping the connection.
- **Opt-in auth for the HTTP server.** State-changing endpoints (`/chat`, `/a2a`, `/webhook/*`) can now
  require a bearer token (`CHIMERA_SERVER_TOKEN`), and the WhatsApp inbound webhook verifies the
  `X-Hub-Signature-256` HMAC when `CHIMERA_WHATSAPP_APP_SECRET` is set — closing the "anyone who can
  reach the port drives the autonomous agent / forges a WhatsApp message" gap. Both are opt-in (unset
  = current localhost-friendly behavior); a public deployment should set them (see docs/security.md).

### Fixed
- **`chimera evolve export` no longer trains on reward-hacked hollow successes.** `curate_sft` /
  `curate_dpo` never checked `diff_productive`, so an empty-diff "success" could become a positive SFT
  example (or the *preferred* DPO response). A `drop_hollow_success` knob (on by default) excludes
  them, matching the refine/RFT diff-gate.
- **Memory store is crash-safe and resilient.** `MemoryStore.save()` is now atomic (temp + replace)
  so a crash mid-write can't truncate the store; `load()` skips a single malformed record instead of
  aborting (losing every memory).
- **Memory ids are full-length UUIDs**, not an 8-char slice that had a ~1% birthday-collision chance
  by 10k memories and silently overwrote a distinct memory on a clash.
- **SQLite `all()`/`by_kind()` return a stable insertion order** (`ORDER BY rowid`), which `value.rank`
  relies on — otherwise `prune` could drop recent, high-value memories.
- **Blank/tokenless recall queries return nothing** on every path (the semantic path used to return k
  arbitrary items while keyword/FTS returned none).
- **The TUI escapes user + model text** before rendering, so bracket input (e.g. `[/]`) can't crash
  Rich's markup parser and content can't forge styling/links.
- **Messaging gateway surfaces a crashed turn** instead of swallowing it as a benign "(no reply)".
- **`MetaAgent.build()` can actually enforce the tool allowlist** when given a registry (previously the
  designed agent was built tool-less, so the isolation safeguard was decorative).

## [0.18.4] - 2026-07-12

**Loop, scheduler & connector hardening.** Two adversarial reviews — of the core agent-loop and of
the scheduler + integrations/MCP surfaces — found ten real bugs the tests missed, all fixed and
regression-tested (fifth review in the series; 27 bugs found across six surfaces so far).

### Security
- **MCP / OpenAPI tool output now goes through the fence + taint ledger.** The untrusted-content
  defense was name-based (a static `FETCH_TOOLS` set), but connector tool names come from the remote
  server — so MCP/REST responses (the *most* untrusted content) were returned raw, unfenced, and never
  marked as a fetch, silently disabling taint escalation for exactly those tools. Connector tools now
  carry an `untrusted_output` marker that `LedgeredTool` honors regardless of name.
- **Connector tools can no longer silently shadow a builtin.** `ConnectorRegistry.into_tool_registry`
  registered with `replace=True`, so a remote server advertising `read_file`/`send_message` would
  overwrite the builtin and hijack every later call. A name collision is now skipped with a warning.
- **Agent-created crons start disabled at the scheduler boundary.** The "self-learned crons start
  disabled pending approval" invariant was enforced only in the learner; `Scheduler.schedule_cron/
  schedule_event(created_by="agent")` now forces `enabled=False` too (defense in depth).

### Fixed
- **`--gen-tests` fail-open (adversarial review of the core agent loop).** When spec-test generation
  produced no tests, the verifier returned a passing result that *supplanted* the Manager review and
  the coverage checklist — a run could be accepted with zero verification. The verifier now ABSTAINS
  (a new `VerificationResult.abstained` flag) and the loop falls back to its other gates.
- **verify-or-revert could delete untouched user files.** When a workspace exceeded the snapshot file
  cap (5000), `restore()` deleted every current file absent from the truncated snapshot — including
  pre-existing files that were never captured. A truncated snapshot now skips the delete pass.
- **`CommandVerifier` no longer crashes the run on a non-timeout `OSError`** (e.g. a removed cwd); it
  reports an unverifiable attempt instead of aborting.
- **MCP tool failures are surfaced as errors.** A `CallToolResult` with `isError=True` was flattened
  to a plain string, so a server-side failure read as a valid answer. It's now prefixed `error:`.
- **Cron store is crash-safe and resilient.** `save()` is now atomic (temp + `os.replace`) so a crash
  or concurrent read can't truncate the crontab and drop every job; `load()` skips a single malformed
  entry instead of aborting the whole load.
- **OpenAPI calls fail fast on a missing required path param** (which would otherwise build a
  different URL, e.g. `/items/` instead of `/items/{id}`) instead of silently hitting it.

## [0.18.3] - 2026-07-11

**Flywheel honesty.** An adversarial review of the self-evolution flywheel — the project's
differentiator — found four ways the "measured, never self-reported" learning signal could be credited
outside the honesty gates. All fixed and regression-tested; the flywheel's statistics reviewed clean.

### Fixed
- **Self-evolution flywheel honesty (adversarial review of the differentiator surface).** Four ways the
  "measured, never self-reported" learning signal could be credited outside the honesty gates — all
  fixed and regression-tested (the flywheel's math — paired stats, Wilson/Newcombe CIs, the diff-gate,
  GEPA — reviewed clean):
  - *Unverified fan-out no longer credits skill promotion:* the hierarchy fan-out recorded a card
    "success" for any non-empty output (no verify-or-revert), and that success rate is the input to
    the auto promote/demote policy — a self-reported signal masquerading as measured. The fan-out now
    records only an advisory experience lesson; card telemetry comes only from verified paths.
  - *Hollow success no longer credits skill promotion:* a hollow success (verifier passed, empty diff)
    was diff-gated out of minting a skill/memory, but still raised the retrieved cards' win rate. The
    card credit is now gated on the same diff verdict.
  - *Memory consolidation no longer launders taint:* merging a cluster wrote the summary as
    `provenance="clean"`, erasing a tainted member's provenance. It now propagates the strongest
    provenance of the cluster.
  - *Transfer gate rejects an empty holdout:* an empty (not just `None`) holdout fell through to a
    zero-task paired comparison and reported "generalizes, transfer measured" — a fail-open that
    disabled the negative-transfer guard. An empty holdout now takes the honest "not measured" path.

## [0.18.2] - 2026-07-11

**Adversarial-review hardening.** Two adversarial reviews — of the governance/security surface and the
hierarchy/orchestration cost accounting — found ten real bugs the test suite missed, all fixed and
regression-tested (the same discipline that caught the fusion bugs in 0.18.1). Plus honest tail-cost
reporting in the cost bench and a friendlier contributor onboarding.

### Added
- **Cost-bench reports the tail, not just the mean.** `chimera cascade-bench` now reports per-arm
  **p50 / p95 / p99 / max** token cost alongside the mean and tokens-per-pass. A cascade can look
  cheap on average while a handful of tasks escalate all the way to fusion — the p95/p99 surface that
  worst-case cost, which is what a token budget actually has to plan for.

### Documentation
- **Contributor onboarding.** A `Makefile` gives the whole quality gate in one command (`make check`)
  plus `install` / `fmt` / `cov` / `docs` / `clean`; CONTRIBUTING now points to it. The
  [Architecture](docs/architecture.md) map was refreshed to cover the newer subsystems — the taint
  layer (ledger / aggregate monitor / drift / quarantine), the delegation cost economics
  (hierarchy / cascade / budget / receipts), the self-evolution flywheel, and start-to-finish project
  autonomy. The docs site now builds `--strict` clean, and the fusion-receipts page is in the nav.

### Fixed
- **Hierarchy cost-honesty fixes from an adversarial review of the orchestration surface.** Four
  issues in the "the hierarchy saved X is auditable" claim, all fixed and regression-tested:
  - *Orchestrator overhead is now metered:* the top-model **decompose** and **synthesis** calls were
    never counted — the reported "measured" cost and saving omitted the hierarchy's own overhead while
    the counterfactual was a full inline agent. Both are now recorded as receipts (counterfactual = 0,
    since a single inline agent pays no orchestration overhead), so they add to the measured cost AND
    honestly reduce the saving.
  - *Counterfactual no longer double-charges context:* each per-subtask inline counterfactual re-charged
    the full ~24k-char orchestrator context, so summing D subtasks over-counted it (D−1)× and inflated
    the saving. The receipt's counterfactual now shares the context across subtasks (a single inline
    agent loads it once); the per-subtask profitability veto keeps full context.
  - *Re-ask is re-audited:* when a spot check caught an unfaithful summary and triggered a bounded
    re-ask, the re-verification skipped the spot check ~80% of the time and could re-accept a still-
    unfaithful summary. The re-ask now forces the spot check.
  - *Cascade weak-tier usage:* a k-sample weak-tier consensus returned only one sample's token usage,
    so a downstream budget/meter saw 1/k of the real spend. The returned result now sums all k samples.

### Security
- **Governance hardening from an adversarial review of the security surface.** Six real gaps the
  tests missed, all fixed and regression-tested:
  - *Taint survives crash/resume:* a run that consumed untrusted content, then crashed and resumed in
    a fresh process, came back with an **empty** taint ledger — a later attempt succeeding off residual
    workspace state finalized as "clean", bypassing the outbound-strip, tainted-provenance and
    pause-on-taint gates. Taint is now persisted in the ordinary checkpoint and re-seeded on resume.
  - *Aggregate monitor sees exfiltration sinks:* the cross-agent split-exfil monitor only counted
    exec/escalation/executable-write as sinks — `send_email`/`http_post`/`post_webhook`/... produced
    **no event at all**, so the exact attack it exists to catch (A fetches a secret, B sends it out)
    passed clean. Outbound sends are now recorded as `send` sink events.
  - *Memory merge no longer launders taint:* `MemoryManager.merge()` dropped provenance, so importing
    another agent's memories stored every fact as `clean` — a poisoned import was recalled as verified.
    Merge now carries `provenance` through (matching the guarantee `remember()` already made).
  - *Drift `absent` fails closed:* a forbidden pattern hiding in an oversized (>1 MB) or undecodable
    file was silently skipped, so the negative (security) check reported "absent" — a falsely-clean
    verifier. An unscannable file now fails the check as un-verifiable.
  - *Data-fence can't be closed early:* `fence()` now neutralizes its own fixed public close marker
    if untrusted content embeds it (otherwise a fetched page containing `<<end-external-data>>` closed
    the fence early and made its trailing lines read as outside the data region).
  - *Taint write-escalation covers self-executing configs:* writing tainted content into `jobs.json`,
    a CI `.yml`, a `Dockerfile`, a shell dotfile, etc. is self-modification too (the scheduler/CI runs
    it next tick) — now escalated, not just `.py`/`.sh`. Content keys also cover `patch`/`diff`/`new_text`
    so an `apply_patch` payload can't slip past taint detection with empty content.

## [0.18.1] - 2026-07-10

**Fusion honesty + orchestration guards.** A maintenance release: an adversarial review of the fusion
surface caught five cost/honesty bugs the tests missed (fake `$0.00` receipts, base64 blobs in judge
prompts, a double-fusion in the "cost-aware" router, plurality-as-majority voting, dates routed as
arithmetic), plus the M19 diff-gate now holds across the human-approval pause and `chimera project`'s
approval controls are guarded. The measured A1 skill-card result is published (default stays OFF).

### Changed
- **M19-A1 measured — reading skill cards stays OFF by default (honest result).** Ran the
  pre-registered paired A/B (`bench/skillcard/`) on a goldilocks model (mistral-small-24b, n=12):
  injecting skill cards lifted accuracy **+16.7pp (66.7%→83.3%)** but the paired 95% CI was
  **[−13.3%, +30.3%] — not significant**, and the token overhead was **+300%**. Both fail the
  registered flip gate, so `CHIMERA_SKILL_CARDS_READ` stays OFF by default and card-reading remains
  opt-in. The number is published either way (no re-rolling for significance).

### Added
- **New example: `media_digest` — video/podcast → transcript → summary.** A real multimodal pipeline
  (`examples/media_digest/`): a script that composes `download_media` (yt-dlp) → `transcribe_audio`
  (faster-whisper, fully local) → an LLM summary, plus the fully-agentic one-liner (`chimera solve
  "download … transcribe … summarize …"`). Validated end-to-end. Added to the examples index.

- **`chimera features` now mirrors every capability the `[full]` extra enables.** The catalog gained
  document reading, media/video download, local speech-to-text, data analysis, and charts — each with
  a copy-pasteable install hint (`pip install 'chimera-agent[documents]'`, etc.) and a check for
  required system binaries (e.g. `ffmpeg`). So `chimera features` is now a live, accurate mirror of
  the README capability table — run it to see exactly what's ready and what each missing piece needs.

### Fixed
- **Fusion honesty & cost fixes (found by an adversarial review of the fusion surface).** Five issues
  the tests missed, all now fixed and regression-tested:
  - *Receipts:* a **priced** model whose provider reported no token usage was pricing to a fake
    `$0.00` — a missing number masquerading as "free", exactly what the receipts module promises never
    to do. It now prices to `None` (unknown); a genuinely `:free` model still prices to a real `$0.00`.
  - *Judge/synth prompt:* a **vision** turn's multimodal content (a list with a base64 image part) was
    being `str()`-dumped into the judge and synthesizer prompts — a base64 blob as "task text". Only
    the text parts are extracted now.
  - *Cost-aware router:* when the agreement gate already escalated a turn to fusion, a failing
    `escalate_on_fail` check could fuse a **second, redundant time** — silently doubling the spend of
    the "cost-aware" router. An already-fused result is no longer re-escalated.
  - *Self-consistency voting:* `majority()` accepted a mere **plurality** (e.g. 2 of 5, the rest
    scattered) as consensus. It now requires a strict majority (> half); weak agreement falls back to
    synthesis, as the module's own "synthesis beats voting" thesis intends.
  - *Router arithmetic gate:* a tight digit-hyphen-digit (`2026-07-10`, `10-20`, a version string) was
    matching the arithmetic detector and routing dates/ranges to expensive fusion. Subtraction now
    requires whitespace padding (`7 - 3`); the other operators stay tight.
- **Diff-gate held across the HITL pause (found by an adversarial code review).** A tainted, *hollow*
  success (verifier passed but the workspace diff was empty) that paused for human approval could be
  learned — written to memory and distilled into a skill — when approved on resume, because the
  diff-gate verdict wasn't carried through the checkpoint. The verdict is now persisted at the pause
  and re-applied on resume, so the M19-A2 anti-hollow-learning gate holds through the approval path.
- **`chimera project` — `deny_card` and `approve_plan` are now guarded.** A wrong/stale card id
  passed to `deny_card` no longer force-escalates a healthy project or loses the real pending card
  (it's a no-op unless the id matches the pending card). `approve_plan` no longer flips a project to
  "running" while a high-risk card is still awaiting approval, avoiding an inconsistent on-disk state.
- **`chimera features` — `youtube_transcript` no longer always shows as "missing".** Its dependency
  was checked by PIP name (`youtube-transcript-api`, with hyphens) instead of the importable module
  name (`youtube_transcript_api`), so `find_spec` never resolved it. Now checked correctly; a test
  guards that every catalog dependency is an import name.

## [0.18.0] - 2026-07-10

**"Batteries included."** One command installs every non-GPU feature, the official Docker image ships
with all of it working out of the box, and every README now walks a complete beginner through what
Chimera can do and how to switch each thing on — in all seven languages.

### Documentation
- **Beginner "what can it do & how" section in all 7 READMEs.** Every README (en/pt-BR/es/fr/de/
  zh-CN/ja) now has a "🧰 What Chimera can do — and how to switch each thing on" section: a
  point-by-point table of every ability with exactly which extra/key it needs and the command to try
  it, a one-line "turn everything on" (`pip install 'chimera-agent[full]'`), and a six-step
  first-time setup for total beginners.

### Added
- **`[full]` extra + batteries-included Docker image.** `pip install 'chimera-agent[full]'` now
  installs every non-GPU feature in one command — messaging adapters, MCP, document ingestion
  (docx/pdf/xlsx→md), media download (yt-dlp), local speech-to-text (faster-whisper), data analysis
  (pandas/scikit-learn) and charts (matplotlib/seaborn/plotly), and YouTube transcripts. The GPU-heavy
  extras (`imagegen-local`, `train`) stay opt-in. The official Dockerfile now builds from `[full]`
  and installs `ffmpeg`, so the container image has vision, audio (speech-to-text + text-to-speech),
  video, documents, data/charts, and the browser all working out of the box.

## [0.17.1] - 2026-07-10

**Patch — `chimera project` resume fix (found by a live test).**

### Fixed
- **`chimera project` — resume after `start --yes` no longer re-asks for plan approval.** A live test
  on the VPS caught it: `project start --yes` auto-approved the plan for that invocation but never
  persisted `plan_approved`, so a later `project approve <id> --card <c>` (a fresh orchestrator that
  recomputes `require_plan_approval` from the durable state) bounced back to "approve the initial
  plan" instead of running the approved high-risk card. The orchestrator now persists `plan_approved`
  the moment a run proceeds past the plan gate, so resumes never re-ask.

## [0.17.0] - 2026-07-10

**"Flywheel & Project."** Two capabilities land together. First, the self-evolution machinery — which
already existed (skills → cards, GEPA, ACE playbook, long-term memory, the diff-gate, the measured
skill lifecycle) — was only ever wired inside `chimera solve` and mostly write-only. M19 Track A turns
the **flywheel** on across *every* autonomous path and makes it read back what it learned, so Chimera
gets better with use — measured and reversible, never on a model's say-so. Second, `chimera project`
adds the layer above the single task: run a whole **project** start-to-finish against a Spec, with the
drift gate as the executable acceptance authority and human approval gates for risky steps. Honest
scope note: this is a **capability** release, not a benchmark headline — the one default that *would*
need a benchmark to justify (reading skills by default) ships as a wired-but-OFF mechanism, awaiting a
paired `skillcard-bench` Δ ≥ 0.

### Added
- **M19-A5 — GEPA refine bridge (`chimera evolve refine`).** GEPA could evolve a skill's prompt but
  nothing fed it from live runs. Now `evolve refine <skill>` mines **verified** trajectories
  (successful, non-hollow-diff) into task instances, GEPA-refines the skill's template, and adopts the
  winner **only through the transfer gate**: it must help its tuned slice AND not regress a disjoint,
  same-capability holdout (the EvoAgentBench negative-transfer guard, which measured GEPA itself
  regressing −12.3 without it). No holdout ⇒ **dry-run** (reported, never persisted). `--apply` writes
  the refined template back (bumped version, usage counters preserved).
- **M19-A6 — auto-rollback (`chimera evolve guard`).** Closes the evolution loop: `evolve guard` runs
  the continuous-evolution benchmark and, on a **statistically significant** regression (the CI lower
  bound on degradation > 0 — never a point estimate) or a cost drift past `--cost-drift-tol`, retracts
  the most recently adopted skill. Retraction is a **retire** (reversible via `skills-approve`), not a
  delete, so an over-eager rollback costs nothing permanent. `--apply` performs it; without it, the
  guard just reports what it would retract.
- **M19 Track B — `chimera project`: run a project start-to-finish against a Spec.** A new
  `ProjectOrchestrator` (`chimera/orchestration/project.py`) drives a whole project above the single
  task by stitching pieces that already existed: the drift **Spec** is the executable acceptance
  authority (the ONLY "done" signal — nothing accepted on a model's say-so), a Kanban **board** is
  the durable task-graph (dependencies in `card.metadata["depends_on"]`; a new `blocked` column for
  cards whose deps aren't met), each unsatisfied requirement becomes a card whose verify command is
  `chimera drift <spec> --only <id>`, and the solve lane (carrying the M19 EvolutionContext) works
  each card verify-or-revert — so running a project feeds the flywheel. The loop runs until the Spec
  aligns or a rail stops it: `max_iterations`, a **high-risk** card awaiting human approval
  (`risk: high` in the spec → deploy/migration/delete pauses), or **no ready card left with the spec
  still unaligned** (a failed card parks in review → escalate to a human). Durable + resumable
  (`home/projects/<id>/`). CLI: `project start` / `status` / `run` / `step` (cron-able) / `approve`
  [`--card`] / `deny --card`. The `Spec`/`Requirement` model gained additive `depends_on` + `risk`
  fields, and `chimera drift` gained `--only <id>` (per-requirement gate for the cards).
- **M19-A1 (mechanism) — the flip-point to couple skill *reading* to skill *evolving*.** A new
  `CHIMERA_SKILL_CARDS_READ` setting: when on, retrieving learned skill cards couples to the same
  condition that lets a run mint a skill (a run that can *write* a skill also *reads* the retrieved
  ones), instead of the independent `CHIMERA_SKILL_CARDS` toggle. **Default OFF** — the mechanism is
  wired and tested, but the default is not flipped until a paired `chimera skillcard-bench` shows
  Δ ≥ 0 (no flipping a default by faith). When flipped, pair it with `CHIMERA_PROVISIONAL_SKILLS` +
  the lifecycle cron so a misfiring card is born on probation and auto-demoted by measured stats.
- **M19-A4 — the flywheel now turns on every autonomous path, not just `chimera solve`.** The kanban
  lanes, workflow `solve` steps, and the SDLC lifecycle crew now build the same six learning seams
  `solve` does (via `build_evolution_context(..., include_memory=True, include_playbook=True)`), so
  working a card / running a workflow step / shipping through the lifecycle **learns** (experience,
  skills, memory, playbook) instead of running a bare, amnesiac agent. The hierarchical orchestrator
  gets the read-and-record half: it injects recalled facts + skill cards into the **top model's**
  synthesis (never the byte-identical worker prefix) and records each run as an experience lesson +
  skill-card credit — but never distils a skill (a fan-out has no verify-or-revert signal, so it
  accrues telemetry only). The honest hierarchy-bench A/B path is deliberately left context-free so
  the measurement stays clean. A new `chimera/evolution/wiring.py` is the single source for *where*
  the memory backend + ACE playbook live; the CLI helpers now delegate to it.
- **M19-A3 — long-term memory readback.** The `solve` path *wrote* verified facts to long-term
  memory but never *read them back*, so cross-run knowledge was write-only. `AutonomousAgent` now
  recalls the relevant facts at the start of a run (duck-typed on `memory.search`) and injects them
  as an advisory "Relevant prior facts" block — sanitized, with tainted facts labelled inline so the
  model weighs them less. Recall degrades to empty on any error (never fails the run); verify-or-
  revert still decides success, so a misleading recalled fact can't corrupt the workspace.

### Fixed
- **M19-A2 — diff-gate the learning (no more hollow-success learning).** A "hollow success" — the
  verifier passes but the real workspace snapshot shows an **empty diff** — no longer mints a skill
  or a memory fact. The flywheel only learns from work that actually changed something. Gated tightly:
  it fires **only** when a workspace guard is present AND the diff is empty; a no-workspace task (e.g.
  a Q&A answer with nothing to diff) still learns as before.

### Changed
- **M19-A0 — shared `EvolutionContext`.** The six self-learning seams (experience buffer, trajectory
  collection, long-term memory, learned-skill distillation + retrieval, ACE playbook) were assembled
  inline only inside `chimera solve`; every other autonomous path built a bare agent that neither
  learned nor read what was learned. A new `chimera/evolution/context.py` bundles them behind one
  factory (`build_evolution_context`) + `apply_to()` (splat into `AutonomousAgent`) + `record_external`
  (let a non-Autonomous path still log an experience row and credit retrieved skill cards). `solve()`
  now calls the factory — behaviour-preserving, and the foundation for turning the flywheel on across
  the lanes/lifecycle/hierarchy (M19 Track A).

## [0.16.3] - 2026-07-10

**"Show me you're listening."** A chat now visibly acknowledges your message the moment it arrives —
the bot shows a typing/working indicator while it thinks, on every messaging transport, so you know it
received you and a reply is on the way.

### Added
- **"Typing…" / working indicators on every messaging transport** — a chat now shows the bot received
  your message and is working on a reply, on each platform the way that platform allows:
  - **Discord** — a real typing indicator (`channel.typing()`, auto-refreshing) around the turn.
  - **Telegram** — the `sendChatAction("typing")` action, re-sent every few seconds (it expires ~5s)
    for the whole turn.
  - **Signal** — the bridge's typing indicator (started, refreshed, then stopped).
  - **Slack** — Slack has no bot typing indicator, so a ⏳ reaction is placed on your message while the
    turn runs and removed when the reply arrives (best-effort; needs `reactions:write`).

  A shared `run_with_indicator` helper runs the (blocking) turn while re-pinging the fading indicators;
  every indicator is best-effort (never fails or delays the reply). The Discord reply flow was factored
  into a testable `DiscordAdapter._respond`. Covered by tests with no network or platform SDKs.

## [0.16.2] - 2026-07-10

**"Nothing shipped stays unreachable."** The final audit follow-up: the three eval modules that were
reachable only from the external `bench/` scripts + tests now have honest CLI surface, so every
implemented capability is usable from the `chimera` command.

### Added
- **`chimera hierarchy-bench --multistep`** — the multi-step companion suite (a single growing context
  vs per-step scoped workers, over large docs), the regime where the hierarchy *actually* saves tokens
  (the single agent re-sends every document on every turn). Live-verified: **+66.5% token reduction** on
  a sample task. Also prices the caching-aware dollar reduction via the caching model. This exposes the
  `hierarchy_multistep` and `cache_cost` eval modules through the CLI (they were reachable only from the
  external `bench/` scripts before).
- **`chimera transfer-gate`** — promote a learned change (GEPA prompt / ACE delta / distilled skill) only
  if it helps its tuned slice AND doesn't regress a disjoint same-capability holdout — guarding against
  *negative transfer* (a change that memorizes its eval). Takes the tuned + optional holdout paired
  pass/fail as JSON (like `bench-compare`), prints PROMOTE / BLOCK with the paired evidence, exits 1 on
  BLOCK. Exposes the `eval/transfer.py` guard through the CLI (was test-only before).

## [0.16.1] - 2026-07-10

**"Audit hardening."** A maintenance release from a full functional audit of the repo (3 parallel
reviewers + live smoke tests across all three tiers). The audit found the core solid — 1295 tests,
`mypy` fully green, `chimera maturity` 37/37 GA, `run`/`fuse`/`solve` verified end-to-end on real
models — and closed the loose ends it surfaced: one shipped-but-unreachable safety feature, two
dishonest feature-catalog entries, three commands that crashed instead of degrading, and an evolution
gate that was narrower than it should be.

### Added
- **`chimera hierarchy-bench`** — the hierarchy paired A/B (single-agent with all docs inline vs the
  orchestrator-worker hierarchy, one worker per doc, same model both arms to isolate the orchestration)
  now has a first-class CLI command, closing the asymmetry where every peer bench (fusion / cascade /
  skillcard / schema / sandbox) had one but the hierarchy's `eval/hierarchy_ab.py` was reachable only
  from the external `bench/` scripts. Quality = paired McNemar/Wilson; tokens = measured totals, no
  significance claim on cost. `--tasks`, `--model`, `--top-model`, `--out`.

### Changed
- **The aggregate cross-agent monitor now runs for the fan-out commands** (`solve-batch`,
  `crew-isolated`) — the v0.16.0 `AggregateMonitor` was built + tested but connected to no orchestrator,
  so its split-exfiltration defense was unreachable in a real run. Each parallel worker now gets its own
  capability ledger, and after the batch the monitor runs over all of them and reports any
  **cross-agent-taint** (one worker fetched untrusted, a different worker sank it) or **fan-out-volume**
  collusion. It is **always on** (pure observability — recording changes no behaviour; it only escalates
  a review note, never blocks). `--taint` now controls only the *stronger* per-worker adaptive allowlist
  (dangerous-when-tainted tools require approval), independent of the monitor. Firing example in
  `docs/security.md`.
- **Collective (cross-model) skill evolution now fires under `--cascade`, not just `--fuse`.** Both routes
  reach fusion over the same multi-model panel at their reasoning peak, so a cascade run now also keeps
  the most *transferable* skill proposal across the panel instead of a single-model one (shared
  `panel_evolution` gate).

### Fixed
- **Honest feature catalog** — `chimera features` no longer advertises capabilities that don't exist:
  `computer_use` (no built-in tool, no dependency in any extra) is removed; `voice_mode` ("full voice
  conversation") is renamed to `speech_io` and described accurately as the `transcribe_audio` (STT) +
  `text_to_speech` (TTS) building-block tools that exist; and `x_search` / `spotify` are labelled
  "pluggable via the OpenAPI→tool importer (no built-in tool)".
- **Graceful missing-key handling** for `skills-evolve`, `playbook curate`, and `rubric-grade` — these
  three model-calling commands previously surfaced an uncaught `MissingCredentialsError` traceback;
  they now print the same clean "No provider key configured" message and exit as every other command.
- **`mypy chimera` is fully green** (219 files) — resolved 3 long-standing type-narrowing errors in the
  scrape module (all runtime-safe `dict.get()`-called-twice patterns, now bound to a local first).
- Added a test for the messaging missing-extra install hint (a previously untested degradation path).

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
