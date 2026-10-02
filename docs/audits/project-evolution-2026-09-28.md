# The project, whole: what it needs to evolve next

Audit dated **2026-09-28**, read from a checkout at `cab4303e` (`main`, v0.63.1 line) plus the
then-open `fix/youtube-transcript-api-1x` branch. Read-only: no claim here was made without
reading the file it cites. This is the same method as `PLAN-right-hand.md` and
`docs/audits/sleeper-channels.md` — an evidence-first study, not a wishlist — turned on the
project as a whole: both product surfaces (Desktop, Terminal), the self-evolution engine and
the governance kernel.

## 0. What this project is, stated honestly

Two products over one engine:

- **Chimera Terminal** — `chimera chat` / `assist` / `tui` / `solve` / `serve` / `crew` /
  `orchestrate` over `chimera/core/`, the Textual TUI (`chimera/tui/`), 79 CLI commands, the
  messaging gateway, the scheduler.
- **Chimera Desktop** — a Tauri + React app (`apps/desktop/`) over the FastAPI/SSE backend
  (`chimera/api/`), shipped both as `pip install chimera-agent[desktop]` and as native
  installers with a PyInstaller-frozen sidecar.

The engine underneath is the differentiated thing: LLM-Fusion with a cost-aware router,
Tier-2 verify-or-revert autonomy, a hierarchical orchestrator with delegation receipts, a
memory manager, a gated self-evolution flywheel (diff-gate, transfer-gate, holdout, lifecycle
policy, skill cards, playbook), a governance kernel with taint tracking, and a
pre-registered benchmark culture (`bench/`, ~100 study folders) that publishes retractions
beside claims.

**On "we are self-evolving because we are working on it with itself."** The claim has to be
split in two, because only one half is demonstrated:

- **Demonstrated:** Chimera *as an operator* on its own repo — PRs #667 and #668 were
  authored by an agent instance running inside the project's own tooling, with the full gate
  (suite, ruff, mypy strict) passing before commit. That is agent-assisted development of the
  agent, and it is real.
- **Not demonstrated:** that the self-evolution engine (`chimera/evolution/`) makes the
  project or its tasks *measurably better over time*. The README says this in its own words:
  seven pre-registered runs, no adequate-powered positive, one positive retracted. The
  flywheel's machinery is GA by the maturity scorecard; its *benefit* is unproven. The
  honest framing is "the agent that builds itself, using a learning engine it has not yet
  shown pays off."

## 1. Maturity signals, measured

`chimera maturity` (snapshot for 0.63.1): **37/37 coverage-IDs proven, level GA, every
surface 1.0, weakest surface: none.** Two honest readings of that perfect score:

1. The scorecard measures *test-file presence*, and its own docstring says so: "a passing
   test presence is a proxy for 'covered', not proof of correctness." A perfect score means
   the taxonomy stopped growing, not that the project is done — the weakest() objective
   that drives the evolution loop now points at nothing, so the loop has lost its target.
2. The taxonomy covers the *engine*, not the *products*. No surface named "terminal UX",
   "desktop UX", "packaging", "release", or "docs" exists in `CHIMERA_TAXONOMY` — exactly
   the areas where the audits below found the gaps. The scorecard is GA because it grades
   what is strongest.

CI (`.github/workflows/ci.yml`) is genuinely strong and *earned*: 3-OS × 2-Python pip-install
smoke, Windows full suite, live integration on main, API types drift, CLI snapshot drift,
`pr_intent_scan` on what a diff brings in, gitleaks, three dependency audits, mutation
testing weekly, desktop release builds with a signed updater and a version-query test on the
frozen binary. Publishing uses OIDC trusted publishing with the UI built in an unprivileged
job. This is well past alpha hygiene; the gaps are not in CI.

## 2. The gaps, ranked by what a user or the project loses first

### P0 — the four things that hurt users or the project today

*Corrected 2026-09-28 (afternoon), same day: the first two items were written from the
README's gap sentence and are now rewritten from the code itself.*

1. **The desktop Code/chat surface carries the kernel — the remaining gaps are narrower
   than the README's sentence.** `chimera/api/code_api.py:assemble_registry` now mounts, in
   order: write region → allowlist → meta-tools → **trust kernel via `govern_step`** → taint
   ledger outermost. The README's gap sentence ("It does **not** reach the desktop app's
   chat … or the OpenAI-compatible endpoint") is stale in its first half: since 0.58.0 a
   policy REVIEW draws an approval card, and since 0.59.0 the desktop starts with the
   kernel in `observe` (fixed signatures refuse, the audit log fills, a path outside the
   project folder asks). What genuinely remains open, each named by the code's own
   comments: (a) the **OpenAI-compatible endpoint** still has no kernel — the true half of
   the README sentence; (b) `ExploreRepositoryTool` builds its own read-only registry
   internally, so its inner calls are governed only as a tool, not per call — the
   `code_api.py` kernel block itself names this as "a real gap; it is just not this
   comment's"; (c) the kernel is off by default (`CHIMERA_GOVERNANCE=off`,
   `config.py:956`), so a stock install gets the taint guard but not the signature rules.
2. **Taint coverage on the TUI and scheduler — the two docs disagree, and the scheduler
   half is open under both readings.** `SECURITY.md` (the always-current policy, coverage-
   by-surface list) still says "**the TUI and scheduler still do not** [track taint]".
   `chimera/cli/right_hand.py`'s module docstring says the TUI builds the full governed
   stack — ledger with conversation-long life, `set_instruction` per turn, a modal
   approver. One of these is stale; resolving it means reading `build_right_hand`'s body
   and the audit log of a live TUI run, not choosing a side. The **scheduler** is open
   under both documents: no surface of `chimera/scheduler/` appeared in either coverage
   list, and a `serve --cron` deployment — the product's own 24/7 pitch — fetches untrusted
   content through job prompts with no ledger, no narrowing, no fence.
3. **The scheduler has no observer** (issue #26, open since 2026-07-31, `help wanted`). The
   project's own deploy doc proves the need: a job that stops firing produces *no verdict*,
   and a job that has failed for a month reads healthier than one that never started.
   `cron doctor` answers when asked; nothing watches. For a product whose pitch is "works
   24/7", the 24/7 surface is the one with no liveness.
4. **Injection's hardest half is open and labeled so** (issue #5): whether the *model* can
   be injected — free-form reasoning over untrusted prose, laundering past the verbatim
   matcher — is unmeasured and unmitigated. Every current number measures an
   already-injected agent. This is the research front where Chimera's measured-methodology
   brand is most at stake, because the defenses it ships are heuristic and say so.

### P1 — product surfaces: desktop

5. **The installers are unsigned** (SmartScreen/Gatekeeper on every first run;
   `desktop-release.yml` calls it out). The updater is signed, the app is not. For
   adoption, this is the single largest friction — and the macOS Intel runner is on a
   deadline (`macos-15-intel` retires ~Aug 2027; the workflow says "revisit before then").
6. **The maturity screen only renders under the Vite dev server** (README: "`chimera app`
   serves the build of production and does not show it, nor does an installer"). The
   project's own self-report is invisible in the shipped app — a wasted honesty asset.
7. **The frozen sidecar is the app's largest trust + size surface** and is only verified by
   CI: the `stt` extra's ffmpeg is excluded (rightly), `documents`/`mcp` ship, and the
   playwright runtime now ships too (PR #667) — but nothing smoke-tests the *frozen* app's
   browser path on an installer build; the repo notes the freeze job is the only verifier.

### P1 — product surfaces: terminal

8. **The right-hand plan's later steps** (persist/undo/verify parity, MCP in `assist`,
   cost in every surface) — the plan itself says Steps 1-2 shipped in #396-#405; the
   remaining steps (session persistence for `assist`/`tui`, the 50-turn cap erasing the
   start of a thread from disk, Ctrl-C semantics) are still listed as the plan's open tail.
9. **`chimera scenarios` is a ruler at ceiling** — the plan proved 4 of 7 checks pass on the
   echo of their own prompt; until a harder daily suite exists, the terminal has no
   regression signal at all.

### P2 — the self-evolution engine: machinery ahead of evidence

10. **No adequate-powered positive for learning.** The bottleneck is the instrument, and
    the README names it: what separates a fixable-by-editing suite from a
    must-invent suite is not captured by any difficulty spec. Building that instrument is
    the evolution work that matters more than another mechanism.
11. **The label loop is built but unfed.** `approvals/history.jsonl` carries `p`, band,
    build beside every human answer — and study 22 found 197 rows, all from taint
    narrowing, none carrying `p`, 99.4% approved: they measure approval fatigue, not
    danger. Nothing fits a map from them outside a bench script. The decisions layer v2
    (`DecisionSpec` registry, fan-out asker, question linter, DecisionLog with outcome
    slots) is designed in `PLAN-study22` with phases still open.
12. **Dedup/curation of learned skills is similarity-threshold-only** (`auto_evolve.py`,
    Jaccard over description words, no model call by design). Cheap and honest, but the
    library will accrete near-duplicates the recall layer must then arbitrate.

### P3 — governance research front

13. **The REVIEW band is built, measured (AUROC 0.871 local) and off by default** — the
    right call until a record-only surface produces real labels. The path forward is
    phase-shaped and already written; it needs the label loop (item 11) first.
14. **The judge stays a library seam** — decided 2026-09-12 with numbers, and the decision
    is *reversible* by the band, not reversed. No action; listed so nobody "fixes" it
    without the corpus that would flip it.

### P3 — everything else, honestly small

15. Six open issues, four of them `good first issue` (#52 11th language, #24 catalogue
    stale entry, #23 recipe, #22 skill card) — all real, all small, none strategic.
16. **Bus factor 1** is named, addressed by scaffolding (`GOVERNANCE.md`), and remains the
    largest *organizational* risk. Not solvable by code.

## 3. What to do, in order

| # | Work | Why here | Evidence it is wanted |
|---|---|---|---|
| 1 | Wire the kernel signature rules into the desktop's Code/chat assembly (with `observe` first, then enforce) | Largest surface, named gap | README §safety; `bench/injection/RESULTS.md` |
| 2 | Taint ledger + fencing into `tui` and the scheduler | Same class of gap, second and third surfaces | `SECURITY.md` coverage table; `sleeper-channels.md` row 6/8 |
| 3 | Scheduler watchdog / liveness (own clock, own process) | The 24/7 pitch is unverifiable without it | issue #26; `docs/deploy.md` §3 |
| 4 | Sign the desktop installers (cert or documented alternative), and plan the Intel-runner exit | Adoption friction, hard deadline | `desktop-release.yml` header |
| 5 | Build the difficulty-spec instrument that makes learning measurable, then re-run the learning A/B | Without it, item 10 stays unanswered and the flywheel stays unproven | `bench/learning_lift/RESULTS.md`; `PREREGISTRATION-v3.md` |
| 6 | Feed the decision label loop: carry `p` on every approval card, fit maps from history | Turns 197 rows of fatigue data into calibration data | study 22 §3; `approvals/history.jsonl` |
| 7 | Ship the maturity screen in production builds + add product surfaces to the taxonomy | The scorecard currently cannot be the evolution loop's objective | `chimera/eval/maturity.py`; README note |
| 8 | Terminal parity tail (persistence for `assist`/`tui`, fix the 50-turn disk cap, Ctrl-C) | Daily-driver correctness | `PLAN-right-hand.md` §2.4 items 7-8 |
| 9 | Measure the injection front (can the model be injected at all) with a published harness | The claim the project leads with is the one it hasn't measured | issue #5; `SECURITY.md` honest limits |
| 10 | Close the four good-first-issues | Cheap, visible, community-signalling | issues #22-#24, #52 |

## 4. What this audit did not do

No code was changed, no number was re-run, nothing was executed beyond reading. Line
references inside the cited audits were re-read where quoted; claims taken from
`bench/*/RESULTS.md` are reported as those files state them, not re-derived.
