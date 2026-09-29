# External study: the harness field, read whole — what to build next

Study dated **2026-09-28**. Companion to `project-evolution-2026-09-28.md` (the inward audit);
this one looks outward: what the 2026 agent-harness field builds, measures and ships, mapped
against what Chimera already has, per surface (Terminal, Desktop, VPS, engine).

## 0. Method, stated honestly

Collected the same day, programmatically:

- **arXiv API**: three queries (`agent harness`, `system prompt`, `llm systems/infrastructure`),
  5 pages each, sorted by submission date → **2,539 unique papers** with title + abstract
  (`study/arxiv_corpus.jsonl`). Scored against 12 axes that mirror Chimera's own surfaces
  (harness/loop, tools/exec, memory, context, prompts, security, eval, self-evolution,
  multi-agent, scheduling/ops, desktop/ux, rag). Top 8 per axis selected → **86 papers read in
  full abstract** (`study/arxiv_top_selection.json`).
- **GitHub API**: repository search for `SYSTEM PROMPT` (8 pages) and `HARNESS` (6 pages) →
  **1,397 unique repos**, ranked by stars, filtered to 1,229 relevant, **top-45 READMEs fetched
  and read** (`study/github_relevant.json`, `study/readmes/`).
- **arXiv recent lists**: cs.MS, cs.PL, cs.OS, cs.PF — current-day listings read; the cs.OS and
  cs.PF abstracts most relevant to agents pulled individually.

**What this study did NOT do**: no full paper was read (abstracts + READMEs only); no number
below was re-derived from the papers' own results; repos were not cloned or run. Claims about
Chimera were checked against the code or the inward audit; claims about the field are reported
as their sources state them. The raw corpus stays untracked in `study/` (data, not docs).

One vocabulary note: "harness" has become a brand word — of the 1,397 repos, a large share are
Claude-Code-ecosystem tooling, resume builders and design tools riding the term. The signal
below is from the subset that actually builds agent scaffolding.

## 1. The field's baseline: what every serious harness now ships

Feature matrix over the 45 READMEs (ECC 269k★, DeepSeek Harness 239k★, deer-flow 83k★,
deepagents 30k★, grok-build 27k★, OpenHarness 16k★, MemOS 12k★, agent-orchestrator 12k★, …):

| Feature | Field | Chimera |
|---|---|---|
| Subagents / delegation | universal | **yes** — orchestrator + crews, receipts |
| Skills library | universal | **yes** — skill cards + playbook + lifecycle |
| Persistent memory | universal | **yes** — memory manager |
| Sandbox / code exec | universal | **yes** — execute_code, sandboxed |
| Checkpoint / resume | common | **partial** — API runs with `thread_id` only |
| Compaction / context offload | common | **yes** — `summarise_compaction`, measured (study 19) |
| Permissions / approvals | common | **yes** — governance kernel + approval cards |
| MCP | common | **yes** — `mcp` extra |
| Cron / scheduling | common | **yes** — scheduler + heartbeat (this PR) |
| Cost receipts | common | **yes** — per-advisor receipts |
| Multi-model routing | common | **yes** — LLM-Fusion router |
| Eval / benchmarks | common | **yes** — pre-registered bench culture (stronger than field) |
| **Lifecycle hooks** (user code on pre/post tool call) | common | **no** |
| **Steering** (redirect a running agent) | emerging | **no** |
| **Plan mode** (explicit plan artifact before execute) | common | **no** (internal plans only) |
| **Plugin marketplace** | emerging | **no** (skill cards are local) |
| **Session sync across devices** | emerging | **no** (persistence is the prerequisite) |
| Provenance / taint tracking | **rare** | **yes** — the differentiator |
| Pre-registered benchmarks + retractions | **rare** | **yes** — the differentiator |

The two right-column "rare" rows matter: the field is converging on the baseline rows; almost
nobody else ships provenance-governed tool use or a retraction culture. Those are the moat; the
plan below defends it while closing the baseline gaps.

## 2. What the papers say, mapped to Chimera

### 2.1 Self-improvement — the field hit our exact failure mode

- **Phantom Guardrails** (2607.13083): self-improving harnesses *hallucinate failures that never
  happened* — the proposer invents violations on legal input resembling a familiar pattern, and
  "fixes" them. Their lab: a deterministic oracle that byte-exactly checks every cited violation.
  → **Chimera's evolution loop has no failure-reproduction gate.** `auto_evolve` distills
  corrections from failures; nothing verifies the cited failure reproduces before the fix is
  accepted. This is the diff-gate idea applied one step earlier (to the *failure*, not the fix).
- **RRSI** (2609.24972) + **ModularRSI** (2609.14857): recursive harness self-improvement
  overfits — in-distribution gains shrink or vanish out-of-distribution; benchmark-disjoint,
  contrastive, *modular* (per-component) edits transfer; whole-harness rewrites entangle.
  → Pairs with the inward audit's item 5 (the difficulty-spec instrument): without an OOD
  instrument, our learning A/B cannot see the overfitting these papers measure.
- **LLM-as-a-Judge Is Not an Oracle** (2609.02246): eleven catalogued ways the judge signal
  fails (bias, harness/metric failures, ground-truth errors, reward hacking — agents reading
  cached answer keys); demote the judge to advisor, gate every change on deterministic
  verification. → Chimera's Tier-2 verify-or-revert already embodies this for *actions*; the
  evolution loop's judge should be held to the same standard. Their 11 failure modes are a
  ready-made checklist for `bench/review_judge`.
- **ACE** (2510.04618): contexts as evolving playbooks; incremental structured updates prevent
  "context collapse" (iterative rewriting erodes detail). → Validates our `evolution.playbook`
  (ACE delta-playbook is already a coverage-ID); their brevity-bias/collapse metrics would
  strengthen the playbook bench.

### 2.2 Harness quality is a measurable, regressable artifact

- **Don't Blame the LLM** (2607.03691): first controlled longitudinal study isolating harness
  contribution — fix the model, vary the harness; practitioners systematically attribute
  harness-caused regressions to the model. → Our receipts carry model + version; they should
  also carry the *harness diff* a run ran on, so a quality regression can be attributed the way
  this paper attributes it.
- **The Scaffolding Matters More Than the Interface** (2608.08654): MCP vs CLI cost estimates
  disagree by an order of magnitude because the *scaffolding* dominates; completion verified by
  repository state, not self-report. → Validates CLI-first tools + our verify-by-state method;
  the MCP extra should stay opt-in and measured, never assumed faster.
- **Claw-SWE-Bench** (2606.12344): an adapter protocol (fixed prompt, runtime budget, workspace
  contract, patch extraction, evaluator) that makes heterogeneous harnesses comparable. → The
  inward audit's item 9 says `chimera scenarios` is a ruler at ceiling with no regression
  signal; an adapter-protocol daily suite is the shape of the fix, and it would let Chimera be
  scored *externally* under the same protocol.

### 2.3 Security — the agent is becoming the kernel

- **When the Agent Becomes the Kernel** (cs.OS recent): agents are kernel-grade principals
  without a trusted mediator; the organizing distinction is *provenance-mediated crossings admit
  deterministic checks; content-semantics crossings do not*. → This is the theoretical frame for
  our taint ledger — and it names our open half exactly: the **scheduler** fetches untrusted
  content through job prompts with no ledger (inward audit item 2), and the model-injection
  question (issue #5) is precisely a content-semantics crossing, unmeasured.
- **Hard Stop** (cs.OS recent): kernel-level preemption and containment for rogue agentic
  execution; autopsy of a 4.5-day sandbox breach (17,600 actions, 136 secrets, VPN enrollment).
  → The VPS surface (`serve --cron`, 24/7, tool-holding) has **no containment story**: no
  egress fence, no per-job resource budget, no kill path. The heartbeat (this PR) *sees* a dead
  daemon; nothing *stops* a live rogue one.
- **HarnessRisk** (2608.17597): safety organized by harness lifecycle — configuration,
  extension, runtime, persistence, action control, incident recovery; 128 sandboxed cases.
  → A lifecycle-organized injection bench is the natural `bench/` study for issue #5, and the
  six phases map cleanly onto our surfaces.
- **MemSecBench** (2607.27080): memory poisoning traced Write→Execute→Forget, with selective
  repair. → Our memory accepts writes from cron job results (untrusted web content) with no
  taint tag and no repair path. A poisoned memory row persists and acts later — the exact
  lifecycle their benchmark probes.

### 2.4 Memory and context — measure before building

- **EvoMemBench** (2605.18421): 15 memory methods vs long-context baselines; *long-context
  baselines remain highly competitive*; memory helps most when context is insufficient.
  → We have a memory manager and no memory-vs-long-context bench. Before adding memory
  machinery, a `bench/memory_lift` study in our own culture would either justify it or retract it.
- **ActKV** (cs.OS recent): KV entries valued by contribution to *action generation*.
  → Not directly actionable (we are a provider client), but the principle maps to compaction:
  keep action-critical spans, drop observation bulk — a measurable compaction-policy variant.
- **Total Cost of Agency** (cs.PF recent): exact attribution of memory-injection cost in
  multi-agent workflows; context-compression gateways costed. → Our receipts could carry
  memory-write and compaction line items, so the cost of "remembering" is on the receipt.

### 2.5 Orchestration — steering and the shepherd pattern

- **PILOT** (2608.26530): *live* self-improvement — a supervisor redirects or aborts the active
  worker mid-run; experience applied while running. → We have no steering on any surface: a
  running `solve`/crew cannot be redirected, only killed. Desktop + terminal gap.
- **SwarmResearch** (2607.02807): shepherd with global context steering a population of search
  agents, each with local context in its own git branch; avoids single-context convergence.
  → Our crews/Agent Manager already do per-task worktrees; the *shepherd* role (global context,
  steering, no local edits) is a concrete pattern `chimera orchestrate` does not yet have.
- **agent-orchestrator (AO)** (12k★ repo): local daemon watching agent activity + git state, one
  live board per project; every worker session carries task, conversation, terminal, changed
  files, PR, CI, review state. → The desktop's Agent Manager has the pieces; the *watcher daemon
  + live board* is the missing shape.
- **Representation Affects Retrieval** (2608.20389): production skill routing — tool-skills vs
  workflow-skills, inlined-body vs summary surfaces in the system prompt; in-context selection is
  small-N retrieval. → Our skill cards face the same representation choice; the inward audit's
  item 12 (Jaccard-only dedup) is the same layer. A representation study would be cheap and ours.

### 2.6 The repos' baseline features we lack, named

- **Hooks** (ECC, shareAI, wshobson, HKUDS all ship them): user-defined pre/post-tool-call code.
  Our governance kernel is the *enforcement* point; hooks are the *extension* point — and they
  must run *through* the kernel, not around it. A `CHIMERA_HOME/hooks/*.toml` surface.
- **Plan mode** (claude-code prompts, gsd): an explicit plan artifact the user edits before
  execution. Our orchestrator plans internally; no surface exposes the plan for edit/approve
  as a first-class step.
- **Checkpoints for terminal runs** (deepagents, grok-build): `RunCheckpointer` exists for API
  runs; `solve`/`tui` don't checkpoint — the inward audit's item 8 (50-turn disk cap, Ctrl-C
  semantics) is the same wound.
- **Memory viewer** (MemOS's dsh plugin): a local UI showing what the agent remembers. The
  desktop has no memory surface; the label loop (audit item 11) needs exactly this visibility.
- **Session sync** (omnigent): start in terminal, continue in browser/phone. Our serve + desktop
  + TUI are three surfaces with no shared session; persistence is the prerequisite.

## 3. The plan

Ranked by (user value × evidence × fit with the inward audit). Items marked ★ pair directly
with an inward-audit item. The moat-defence rule: nothing below trades away provenance,
receipts or the bench culture to chase the baseline.

### Chimera Terminal

1. **Hooks surface** — user TOML hooks on pre/post tool call, executed *through* the governance
   kernel (a hook is untrusted code: taint-tagged, sandboxed, receipted). The field's baseline
   we lack; our enforcement point already exists, so this is exposure, not new machinery.
2. **Session persistence + checkpoints for `solve`/`tui`** ★(audit 8) — `RunCheckpointer` for
   terminal runs; fix the 50-turn disk cap; Ctrl-C semantics. Prerequisite for session sync.
3. **Steering** — inject a message into a running run (PILOT). Terminal first (`chimera solve
   --steer`), desktop second.
4. **Plan mode** — the orchestrator's plan as an editable artifact the user approves before
   execution (OSFoundry's persistent-intent insight, claude-code's plan-mode UX).
5. **Adapter-protocol daily suite** ★(audit 9) — replace the at-ceiling `scenarios` with a
   Claw-SWE-Bench-style contract (fixed prompt, budget, workspace, patch extraction, evaluator);
   publish Chimera's own scores under it.

### Chimera Desktop

6. **Kernel on the OpenAI-compatible endpoint** ★(audit 1a) — still the top P0; nothing in this
   study changes that, it only reinforces it (HarnessRisk's "configuration" phase is where
   unmanaged endpoints fail).
7. **Memory inspector + approval-history viewer** ★(audit 11) — what the agent remembers and
   why it asked, visible; the label loop needs to be seen to be fed (MemOS viewer as prior art).
8. **Steering + live board for crews** ★(audit — desktop surface) — redirect active runs; one
   live board per project (AO's shape): task, conversation, changed files, PR, CI per worker.
9. **Ship the maturity screen in production builds** ★(audit 7) — cheap; the honesty asset is
   currently invisible in the shipped app.

### Chimera VPS (serve + scheduler)

10. **Containment for cron jobs** (Hard Stop) — egress fence per job class, per-job resource
    budget, `chimera cron kill <job>` + a containment receipt. The 24/7 surface is the one with
    no stop story; the heartbeat (this PR) sees death, nothing prevents rampage.
11. **Taint ledger on scheduler job prompts** ★(audit 2) — job results (untrusted web content)
    enter memory and later actions with no provenance; the "kernel" paper's frame says this is
    a provenance-mediated crossing we already know how to check.
12. **Memory poisoning defence** (MemSecBench) — tag memory writes from untrusted sources;
    a selective-repair path (`forget` with provenance). Pairs with 11.
13. **Deadline/priority classes on cron jobs** (SARA, cs.PF) — small, real, fits the scheduler
    we just made observable.

### Engine (cross-surface)

14. **Failure-reproduction gate in the evolution loop** (Phantom Guardrails) — before accepting
    a proposed fix, reproduce the cited failure against a byte-exact oracle; a fix for a
    failure that never happened is a retraction, not a win. This is the single most
    culture-aligned item in this study.
15. **Difficulty-spec instrument, then benchmark-disjoint modular RSI** ★(audit 5; RRSI/
    ModularRSI) — the instrument first (it is what makes OOD shrinkage visible), then adopt
    benchmark-disjoint, per-component edits in the loop.
16. **`bench/memory_lift`** (EvoMemBench) — memory manager vs long-context baseline, our
    methods, pre-registered; justify or retract the memory machinery.
17. **Harness-version longitudinal receipts** (Don't Blame the LLM) — every receipt carries the
    harness diff it ran on; regressions attributable to the harness, not the model.
18. **Lifecycle injection bench** ★(audit 4 / issue #5) — HarnessRisk's six phases as the
    study shape for the model-injection question; the claim we lead with is the one unmeasured.

### Deliberately not planned now

- **Plugin marketplace** (DeepSeek Harness's "everything is a plugin"): real, but bus factor 1
  (audit item 16) — an ecosystem commitment before a second maintainer is how moats rot.
- **KV-cache-level serving work** (ActKV, KV memory wall): we are a provider client; the
  actionable residue (action-critical compaction) is folded into item 16's bench design.
- **Multi-device session sync**: item 2 is its prerequisite; sync without persistence is a demo.

## 4. What this study did not settle

- Whether hooks-through-the-kernel is fast enough to be worth having (a hook that adds 200 ms
  to every tool call will be disabled by its users — measure before shipping).
- Whether the shepherd pattern beats our existing crew check on anything we actually run —
  it is a pattern from open-ended optimization tasks, not from daily-driver coding.
- Whether memory-vs-long-context favours our memory manager — EvoMemBench's own result
  (long context competitive) suggests it may not; that is a legitimate bench outcome.
