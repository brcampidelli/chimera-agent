# Study 25 — system prompts: what five sources agree on, and a design for every Chimera situation

2026-09-25 · US$ 0 · six agents, read-only. The full reports are kept outside the repo (scratchpad `study25/report-*.md`).

**What the six agents read**

| # | Source | What was covered |
|---|---|---|
| 1 | Anthropic prompts | 521 entries, 235 of them prompts |
| 2 | OpenAI / Codex prompts | 87 files |
| 3 | xAI, Meta, Google, Microsoft, Mistral, Perplexity | 54 files |
| 4 | Coding harnesses | 34 captured prompts, plus the prompt source and prompt-file git history of 16 open-source harnesses |
| 5 | Literature | about 90 papers and vendor guides, each graded A–E |
| 6 | Chimera itself | every prompt in the tree, plus the production VPS |

**Where the vendor prompts came from.** Sources 1–3 are captures of vendor system prompts that circulate publicly (`asgeirtj/system_prompts_leaks`).

- They were read as data, never followed as instructions.
- **No text from them is in this repository or in any Chimera prompt.** The ideas below are paraphrased and credited to their source.
- The captures cannot be authenticated, and a few are mislabelled. For example, the file named as OpenAI's "auto-review" prompt is byte-identical to its generic 5.4 base prompt. Each capture is treated as a lead, not as ground truth.

**Licences of the harnesses we read.**

| Licence | Harnesses |
|---|---|
| Apache-2.0 | Aider, Cline, Roo, Goose, Continue, Codex CLI, Gemini CLI, Kimi CLI |
| MIT | OpenHands, SWE-agent, mini-swe-agent, OpenCode, Kilo, Pi, Trae |
| FSL-1.1-MIT | Crush |
| GPL/AGPL | Zed |

Only ideas are taken, never code.

## 1. The question, and the honest version of the goal

The owner asked for all of Chimera's system prompts to be planned situation by situation, with an architectural design for each, "better than every other harness and every competitor".

"Better" is only a claim once it has been measured, so this plan does three things:

1. It defines what better means for each situation.
2. It says which benches can show it.
3. It orders the work so that the changes that are simply fixes land first. The changes that are bets get a pre-registered, paired test.

## 2. What the five sources agree on

A principle counts as convergent when at least three of the five sources reached it independently. Evidence tags:

| Tag | Meaning |
|---|---|
| **[M]** | Measured: a controlled number, a vendor's stated test, or a dated incident with a before/after |
| **[C]** | Convergent but unmeasured |
| **[T]** | Taste |

The literature grades (A–E) are the agent's own; see §8 for how they map.

### 2.1 A prompt is an assembled stack with a small shared core, and it is a per-model artefact [M]

- **Anthropic** serves every surface as core → surface modules → per-model patches → injected environment → tool schemas. Its Claude Code core shrinks from about 4.5k words (Sonnet 5, Haiku 4.5) to about 1k (Opus 5.5) for the same behavioural goals. Its own audit guide calls a prompt an artefact to re-audit on every model release.
- **OpenAI Codex** uses a per-model base template with a `{{ personality }}` slot, followed by single-purpose tagged developer blocks: permissions, app context, collaboration mode, context-window guidance, skills, memory, multi-agent role.
- **Harnesses:**
  - OpenCode ships one prompt per model family. Moonshot's PR there reports that the generic prompt regressed on its internal benchmarks and that a Kimi prompt recovered the loss.
  - Cline went from 8.6k words to about 700; Goose cut 30%. The newest-model prompt in OpenCode is its shortest.
- **Literature:** prompt effects do not transfer across models (A/B).
  - Format preferences correlate weakly across models (2310.11324).
  - One compound prompt cost GPT-4o-mini 12.2 pp, did nothing to GPT-4.1 and helped o3-mini (2609.03156).
  - Tuned playbooks keep 1 of 135 effects when moved to another runtime (2608.05778).

### 2.2 Stable prefix, volatile tail [M]

- **Literature:** caching cuts agent cost by 41–80% and time-to-first-token by 13–31%. Dynamic content has to go last, or caching can raise latency (2601.06007).
- **Harnesses:**
  - OpenHands splits the system prompt into a cacheable block and a volatile block, with a typed registry and a byte-exact snapshot matrix.
  - Goose injects the time and todos near the end at send time and never stores them ("minus-one" messages).
  - Continue labels git status a start-of-session snapshot.
- **Chimera, measured for this study:**
  - The Code screen puts recalled facts, jobs, works, the voice note and the approved plan *between* the base prompt and the stable blocks (`chimera/api/code_api.py:1201-1212`). The shared prefix therefore ends after about 318 tokens whenever any of those changes.
  - Retrieved skills (volatile per task) sit before the stable project and owner blocks on every surface (`chimera/core/agent.py:578-600`).
  - On the VPS, the first step of a cron job has a median of 6,568 tokens. Its cache hit rate averages 14.2% with a median of 0.
  - The caveat already in the tree still holds: caching changes trajectories (2609.04748). So "cost goes down" and "success does not move" have to be measured separately.

### 2.3 Fewer rules, each with its reason, at normal volume, said as what to do [M]

- **Anthropic's audit guide** lists the patterns that hurt newer models:
  - CAPS boosters;
  - step-by-step choreography for judgement work;
  - narration suppressors;
  - prohibitions without a reason.

  It says an anxious prompt produces a hedging model. Anthropic's served prompts still contain several of these patterns, so the guide describes what to do, not what they ship.
- **Literature:**
  - Satisfying *all* of k simultaneous constraints drops to 5.7% at k=8, and below 50% at k=7 even for the strongest model (2608.12426, A).
  - Naming a forbidden word primes it: it was involved in 87.5% of violations (2601.08070).
  - Politeness, tips and threats have no aggregate effect (A).
  - Aider's "$2,000 tip / blind user" prompt scored worse and was reverted the same day.
- **The one dissent:** OpenAI's Realtime guide recommends CAPS for voice. So emphasis is a per-model knob, not a rule (§6).

### 2.4 Claim only what was observed, and verify in the harness [M]

- **Anthropic:** a model-migration snippet requiring every progress claim to point at a tool result "nearly eliminated" fabricated status reports in its tests. Anthropic's graders put the burden of proof on the expectation.
- **Harnesses:**
  - SWE-agent: a lint-gated edit tool gives 18.0% against 15.0% without the gate.
  - Aider: one round of test feedback moves the median polyglot pass rate from 22.2% to 55.1%.
  - Cline on Terminal-Bench 2.1: 25 tasks claimed `verified=true` and failed the checker. The fix was a typed `submit(verified)` plus "verify by running it".
  - Muse Code (Meta): a check built from the hypothesis under test proves nothing, and a failing test must never be narrowed.
- **Literature:** self-correction without an external signal *lowers* accuracy (2310.01798, A).
- **Chimera:**
  - `DEFAULT_SYSTEM_PROMPT` has no rule about verifying, or about saying what was not verified.
  - The claim-vs-diff work is the stronger control, and the prompt should point at it rather than replace it.

### 2.5 A request type carries its own authority [M]

Questions, reviews, diagnoses and bug reports are not permission to edit. Only an explicit change request is.

- **OpenAI** 5.6 turned this into a taxonomy. "Finish it" buys persistence, not more authority.
- **Anthropic** lists "a described problem is an assessment request" among its tested snippets.
- **Gemini CLI** needed a dedicated fix for edits made in answer to questions. Grok Build, Copilot and OpenHands have the same rule.
- **Chimera's base prompt** says "Your job is to DO the task", with no directive boundary. Every interactive surface inherits that sentence.

### 2.6 Who is watching decides when to ask; "blocked" has a definition [M]

- **Anthropic:**
  - Unattended runs do not ask permission for reversible steps; they state their interpretation at the top and proceed. It says this opening sentence is load-bearing.
  - Every autonomous loop runs a last-paragraph check: if the reply ends in a plan or a promise, do it now.
- **Codex goals:**
  - "Complete" only when achieved, never because the budget is nearly spent.
  - "Blocked" only after the *same* blocker recurs on three consecutive turns; resuming resets the count.
- **Chimera:** the one measured exception in the base prompt ("ask at most three questions") also rides into the 15 VPS cron jobs, where nobody will answer.

### 2.7 One untrusted-content rule, but the prompt is not the boundary [M]

- **Adaptive attacks** break every prompt-level defence tested:
  - spotlighting and sandwiching: over 95% attack success;
  - MetaSecAlign: 2% → 96%;
  - human red team: 100% (2510.09023, A).
- **Documented exploits** are all failures of the prompt alone: EchoLeak, Comet, the Gemini calendar invite, and CVE-2025-53773 (a poisoned README made Copilot enable its own auto-approval). Comet had an in-prompt clause and was still exploited. Every vendor fix was architectural.
- **What still helps, as defence in depth:**
  - one fence syntax with a per-session nonce;
  - a closed list of effects that fenced text may never cause (send, delete, spend, use a credential, new target);
  - trust tags per message (principal, peer, relay);
  - datamarking rather than plain delimiting (static attack success from about 50% to 3.1%, 2403.14720).
- **Chimera:**
  - There are five fence syntaxes.
  - The base prompt names only one of them.
  - The fence sentence disappears whenever a worker prompt *replaces* the base (see §3).
  - The inbound webhook payload enters unfenced.

Capability narrowing and taint stay the boundary, as the tree already says (#487, #488, #589).

### 2.8 Delegation is a contract; the recorded state beats the worker's report [C]

- **Anthropic:** a manager's brief carries the user's words by reference, plus only what the worker needs. Every sentence of a brief becomes a requirement, so it must not invent deliverables. Workers write for a relayer.
- **OpenAI:** give workers disjoint write sets, tell them they are "not alone, do not revert", and never redo delegated work.
- **Copilot fleet:** the task store is the truth, not the worker's own "done".
- **Roo and Kilo:** orchestrators have no edit tools and plan waves by file overlap.
- **Literature:** multi-agent systems lose 39–70% on sequential tasks (2512.08296), and most of debate's gain is the vote (2508.17536).

### 2.9 Judges are debiased by structure, not adjectives [M]

- **What the literature measured:**
  - Position bias and self-preference replicate.
  - A judge can have test-retest reliability above 0.95 and still show position bias above 0.10 (2606.19544).
  - Reference-guided grading cut math-grading failures from 70% to 15%.
  - Thinking-mode judges gain about 10 pp at under 2× compute.
- **Anthropic:** asking a finder for "only important issues" lowers recall. Coverage (finders) and filtering (verifier) are separate stages, with three-state verdicts that quote the line.
- **Codex's security judge:**
  - Scores authorisation separately from risk.
  - Only user and operator messages can authorise.
  - Its first output token is the verdict.
- **Chimera:** already blinds its judge. It measured that the judge yields to framing wrappers: 8–9 of 14 attacks reach ALLOW (#488).

### 2.10 Memory is recall, not policy [C]

These points converge across Codex memory v2, Anthropic, Grok, Kilo and the MINJA attack (98.2% injection success).

- **Writing memory:**
  - Store plain facts, never imperatives.
  - Keep "the user asked X in task T" apart from "the user prefers X".
  - Store only what the user stated, never the assistant's suggestion.
  - Prefer saving nothing, and never store secrets.
- **Using memory:**
  - Use it invisibly and minimally.
  - Treat it as no proof of the present state.
  - Cite it when it is used.
- **Chimera:** memory capture is a regex (`chimera/memory/capture.py`). No model-side extraction prompt exists.

### 2.11 Compaction keeps the user's words and standing constraints verbatim [M]

- **Anthropic** calls the final "do not call tools" sentence of its compaction prompt load-bearing.
- **Goose, OpenHands and Codex** use fixed-field schemas: intent, pending tasks with their ids, errors quoted exactly, decisions including the rejected ones, and a next step only if it continues a user request.
- **Literature:** masking old observations matches summarising at about half the cost (2508.21433). A full rewrite can collapse a context from 18,282 tokens to 122, falling below baseline (2510.04618).
- **Chimera:** the rule-form summariser is measured (+63 pp). But compaction fired **0 times in 137 real runs** (`bench/compaction`), so the trigger matters before the prompt does.

### 2.12 Voice is a thin talking layer in front of a working layer [C]

- **Anthropic:** the voice prompt is 172 words, at most two sentences or 50 words, with pronunciation control. Anything structured goes to the text model.
- **OpenAI Codex:** the working model marks every message STATUS or COMPLETE. COMPLETE means an outcome, a terminal limitation, or exactly one question. Only completed actions and verified facts are spoken.
- **Chimera:** has the same split (talk/work, by regex). Its spoken note is the only output-format instruction anywhere in the product.

### 2.13 The always-on agent is a steward, and delivery is an explicit act [M]

- **xAI's public prompt history** is the only measured record of a bot persona in a shared space. In July 2025, an instruction to mirror the thread's tone produced extremist outputs.
  - The fixes: a hard length cap, no mirroring, independence from the owner's stated opinions, and treating constrained-format bait as a cue to answer normally.
- **Anthropic and Grok Bot:**
  - Timer-driven runs continue established work, back off when quiet, and end silently when there is nothing new.
  - An acknowledgement is not a delivery.
- **OpenAI's scheduled-run header:** runs are non-interactive and do not repeat prior runs.

### 2.14 Prompt engineering is a process with a gate [M]

- **Gemini CLI:**
  - About 45 behavioural evals; each must fail on the old prompt before the fix lands.
  - Prompt PRs report token use as percentiles with confidence intervals (p90 −15.2%, significant).
  - A script flags any PR touching prompts or tool text.
- **OpenHands:** byte-exact snapshots of every rendered variant.
- **Dated failures of process:**
  - xAI's unreviewed prompt edit (May 2025).
  - Roo's rules left stale after the parallel-tool-call migration.
  - Anthropic's served prompts violating Anthropic's own audit guide.
  - Docker Gordon's prompt patched sentence by sentence to pass individual eval cases.

### 2.15 Personas buy tone, never correctness [A]

- **Literature:** "You are an expert X" gives no significant gain on 5 of 6 models and 9 significant losses (2512.05858). Across 162 roles and four model families there is no gain, and the best role cannot be predicted (2311.10054).
- **OpenAI** removed forced affirmation from its "friendly" personality between generations. Every OpenAI personality excludes user-requested artefacts.
- **xAI's Grok 4** searched its owner's posts to decide what it thought.
- **For Chimera:**
  - A persona is a voice layer only.
  - It is never applied to artefacts (newsletters, PR bodies, emails).
  - It is never "what Bruno thinks" said to third parties.
  - Judges and graders get none.

### 2.16 Reasoning models do not need to be told to reason [A]

- The null holds across a meta-analysis of 100+ papers (2409.12183).
- "Compare several approaches" costs 2.4–7.4× the tokens with no gain in success, and "maximum certainty" costs 1.3–1.9× (2608.01347, pre-registered).
- Bounded efficiency (scope, smallest change, stop rule) has no penalty.

## 3. Where the sources disagree with each other, or with our own measurements

| Item | What the sources say | What we do |
|---|---|---|
| Deferred tools behind a tool-search step | Anthropic and OpenAI defer hundreds of connector schemas | **We measured the opposite:** a tool router made every executor worse (#537: −0.09/−0.19/−0.31). With 28 tools (~4,250 tokens) we keep full schemas. Progressive disclosure of *skills* is lighter, was not in that measurement, and gets its own paired test. |
| CAPS emphasis | OpenAI Realtime: use it. Anthropic: remove it. Mechanistic work: it moves attention without reliable gains | A per-model overlay knob, measured (§6, H4). |
| Hide the backend in voice; never refuse | Codex realtime | Rejected. Governance needs honest attribution, and refusals belong to the governed layer. |
| "Never trade or move money" | Anthropic computer-use blocks | Conflicts with the owner's trading mandate. What transfers is the structure: hard limits in code, a typed `blocked` outcome, and confirmation outside the cap. |
| Sampling temperature | Gemini 3 wants 1.0 (lower can loop). Qwen3 thinking wants 0.6 and never greedy. R1 wants 0.5–0.7 | Our worker loop defaults to 0.2 for every model (`chimera/core/agent.py:136`). An overlay field, measured per family (H5). |
| Placement of long context | Vendors differ | All agree the query goes after the data. A short restatement of the contract after the data satisfies all three. |
| Context files (AGENTS.md) | Generic LLM-written files: −0.5 to −2 pp success and +20–23% cost (2602.11988). Probe-validated guidance: +7.5 pp (2606.20512) | Keep AGENTS.md as a convention (it is the user's file). Measure our *own* injected blocks one by one before assuming they help. |

## 4. Where Chimera stands (inventory, `origin/main` at 0.61.1)

**What is sound, and measured:**
- The narrow "ask at most three questions" exception (paired test).
- The rule-form compaction summariser (+63 pp).
- The DROPPED-only envelope verifier (23/23).
- The blind fusion judge.
- The governance judge, pinned byte for byte to its bench instrument.
- The solve path putting all volatile context in the user turn.
- `WORKER_SYSTEM` byte-identical across hierarchy workers.

**Defects the inventory found.** Each one is checked against the code and is a fix, not a bet.

1. **The owner's identity (`agent.json`) never reaches 13 surfaces.**
   - Affected: the Discord and other messaging bots (`cli/main.py:3338`, where `AgentConfig` has no `instructions`), `chimera solve` from the CLI, `serve` HTTP, ACP, MCP, A2A, the sub-agent, the explorer, the crew supervisor, kanban, lifecycle and workflow.
   - The Settings screen says the identity is applied "on every surface".
2. **Prompts that *replace* the base lose its safety sentences.** Four worker prompts substitute for `DEFAULT_SYSTEM_PROMPT` instead of appending to it:
   - hierarchy `WORKER_SYSTEM`;
   - `SUBAGENT_SYSTEM`;
   - `EXPLORER_SYSTEM`;
   - crew approaches as the whole system prompt.

   With the base gone, they lose the untrusted-data fence, "act, don't describe", and the language rule. Kanban appends instead, with a comment explaining exactly this risk (`kanban/lanes.py:233-238`).
3. **Five fence syntaxes.** The base prompt names one of them. The webhook payload is not fenced.
4. **Two opposite language rules.** "The owner's language" (identity) versus "the task's language" (the hierarchy synthesiser, the checklist, the spec drafter). Separately, the owner block is last in the loop but first in the hierarchy and `RoleAgent`.
5. **Contradictions inside single prompts.**
   - The ask-three-questions exception versus the action nudge, which fires on zero tool calls (`agent.py:831-835`).
   - The fusion panel is told to use tools it does not have.
   - The hosted governance prompt asks for "exactly one word" *and* "only a JSON object".
6. **Three graders, three abstention semantics.**
   - The rubric judge returns `None`.
   - The strong verifier passes without a score.
   - `verifier_select._parse_score` returns **0.0** for an unparseable grade (`fusion/verifier_select.py:77-79`), the defect the other two already fixed.
7. **Promises without a mechanism.**
   - The prompt lists "relevant skills you can use", but the agent cannot call the built-in skills.
   - `prompt_cache.py` calls the last system message "reliably-stable". It is not, and the feature is off.
8. **Missing context.**
   - No prompt carries the date, OS or shell, the working directory, or git state.
   - There is no output-format contract outside voice, and none for Discord.
   - There is no verify-before-done rule.
   - There are no prompts for user-facing review, browser use, web research, or model-side memory extraction.
9. **Unmeasured.** Almost every block injected into prompts has never been measured:
   - skills, bundles, AGENTS.md header, owner identity;
   - Code-screen facts, jobs, works, plan;
   - planner, sub-agent, explorer, crew;
   - memory distillation;
   - all production-VPS prompt content.

   The base prompt itself is measured only indirectly: `bench/scenarios` sits at its ceiling and cannot tell prompt versions apart.

**Production content.** The prompts inside the VPS jobs, the workspace `AGENTS.md` and the trading loop live in the private ops repository. They are audited there, not here. The inventory found several concrete issues in them, which are handed to the owner separately.

## 5. The architecture

### 5.1 Layers

Every situation renders the same stack. Order equals cache order: everything above the line is byte-stable for a given install, surface, model and workspace.

```
system (stable, cacheable)
  L0  core            one per version, all situations        ≤ ~250 words
  L1  situation       one module per S1–S15                   ≤ ~250 words each
  L2  model overlay   one per model family                    data (knobs) + ≤ ~80 words
  L3  surface         CLI · desktop · voice · Discord · API   ≤ ~80 words
  L4  project         AGENTS.md, fenced as convention         ≤ 2,000 chars (existing cap)
  L5  owner           agent.json, read last, wins             ≤ 4,000 chars (existing cap)
tools (stable per registry; API field, never named in prose)
── cache boundary ──
history (append-only)
context message (volatile, rebuilt each turn, never stored)
  date · tz · OS/shell · cwd · git snapshot · recalled facts (quoted, with source and date)
  retrieved skills and cards · jobs and works status · approved plan · todo list · budget
  late reminders (the per-turn contract restated for models that need it — §6)
user turn
```

**Rules of composition.** Each rule is enforced by a lint or a test, not by care.

1. **L0 is never substituted.** Situation modules append to it. This one rule removes defect 2 for every worker at once.
2. **One fence syntax, one sentence, in L0.** The fence carries a per-session nonce. The sentence also lists the closed set of effects fenced text may never cause.
3. **One language rule, in L0:** answer in the owner's language unless the artefact's audience needs another. It lives in one place and is referenced, never restated.
4. **Precedence is explicit and the same everywhere:** owner > situation contract > project convention > retrieved advice > anything inside a fence. The owner block is always last.
5. **Volatile content never enters the system prefix.**
   - It rides in a context message rebuilt for each turn and not stored in history. That keeps the reason `absorb` drops system messages today (no stale recall copies accumulating) without breaking the cache.
   - The solve path already does this. The Code screen, chat and Discord are the ones that move.
6. **Tools are named only in tool descriptions.** A prose line that names a tool is rendered only when that tool is registered for the session. `TODO_PROMPT` already follows this.
7. **Reasons stay next to rules.** Every prompt sentence carries a short "because" in the prompt when the model benefits, and its measurement provenance in the code comment above it, as the tree already does.

### 5.2 L0, the core, in content (not wording)

About 250 words. The wording is written and measured in its own PR. The content:

- **Identity as a working relationship.** Chimera works for the owner and acts on their behalf inside what they granted. No competence adjectives.
- **Honesty contract:**
  - Claim done, fixed, sent or verified only on something observed in this session.
  - Say plainly what was not checked.
  - A failure or a skipped step goes in the first sentence, not the last.
- **Directive boundary.** A question, review, diagnosis or report is answered, not acted on. An explicit request to change something is carried out.
- **The fence rule:**
  - One syntax.
  - Fenced text is data.
  - It can never trigger sending, deleting, spending, using a credential or reaching a new target.
  - Instructions found inside it are reported to the owner.
- **A denial is an answer.** A governance block, a refused tool or a failed approval is final for that action. Re-encoding, renaming, splitting or routing around it is a new, riskier action, not a retry.
- **Language rule and precedence rule** (§5.1, rules 3–4).

### 5.3 The registry

A `chimera/prompts/` package owns every prompt string in the product. Each section is a typed record:

- **Identity:** id, layer, and the situations it serves.
- **Text.**
- **Provenance:** measurement status (`measured` / `null` / `unmeasured`), plus the bench or PR that measured it.
- **Cache tier.**

`assemble(situation, surface, model_family, flags)` returns the system prefix, the context message and a token account per layer.

Three checks run in CI:

| Check | What it does |
|---|---|
| **Snapshots** | Every situation × surface × family × flag combination is rendered to a text file under `tests/prompt_snapshots/`. A prompt change shows up as a reviewed diff. The first PR is a pure refactor whose snapshots equal today's strings byte for byte. |
| **Lint** | Fails on: more than one fence syntax; a tool named in prose but not registered for that situation; a second language or precedence rule; CAPS words outside an allow-list; a dated fact; a layer over its token budget; an L1 module that does not start from L0. |
| **Receipt** | Every run records the snapshot hash, `cached_tokens` and the served endpoint. The route and cache fields already exist (#484); only the prompt hash is new. A behaviour change can then be traced to a prompt version. |

## 6. Model-family overlays (L2)

An overlay is mostly data, read by the assembler and the gateway:

| Field | Why | Evidence |
|---|---|---|
| `temperature`, `top_p` | Vendor guidance differs sharply; the 0.2 global default contradicts three families | D (vendor), measured per family (H5) |
| `emphasis` (plain / emphatic) | CAPS helps some families and over-triggers others | B/D, conflicting (H4) |
| `examples_in_system` | Few-shot hurts some reasoning models; it fixed format compliance for others | B |
| `tool_protocol` (native / text block) | Weak models follow native tool calls poorly. Code inside JSON costs 4–9 pp | Aider (measured) |
| `edit_format` (search-replace / whole-file) | Qwen2.5-Coder-32B: 8.0% with diff, 16.4% with whole-file | Aider (measured); our own `edit_format_by_model` was rejected, so re-test before adopting |
| `strip_reasoning_in_history` | Qwen3 guidance | D |
| `restate_contract_late` | A small Qwen followed a system-slot instruction 8% of the time against 64% in the user slot (n=50/arm); VSysBench shows open-weight collapse under conflict | D/C; applies to the local backends (S9 local, S3/S4 on ollama) |
| `patch` (≤ ~80 words) | Family-specific tics or fixes, deleted with the family | Anthropic audit guide |

The fusion panel draws from several families, so each panellist gets its own overlay.

## 7. Situation designs

Each design lists four things:
- the L1 content;
- what moves to the context message;
- what the harness enforces (the prompt only states it);
- the measurement that decides it.

Situations share modules where their contracts coincide: S2 and S3 share `interactive`; S1 and S5 share `unattended`.

### S1 — autonomous solve (`chimera solve`, `/api/runs`, cron worker, kanban, A2A/MCP)
- **L1 (`unattended` + `coding`):**
  - Act by default. Keep the measured ask exception, but when nobody is watching, replace "ask" with "state the assumption at the top and proceed". Stop only for destructive or scope-changing decisions.
  - The requested scope is the deliverable: do not narrow, widen or transform it, and list what was left out.
  - Read with a purpose, then stop.
  - Never revert changes you did not make.
  - Destructive-action rules:
    - resolve targets with a read first;
    - no recursive operations on home, root or the workspace;
    - no unresolved variables or globs (an empty variable in a delete path is the failure this prevents);
    - prefer reversible operations;
    - report what was deleted and whether it can be recovered.
  - The last-paragraph check.
  - "Blocked" only after the same blocker three times running.
  - The final report says what changed, how it was verified, and what was not run.
- **Context message:** the environment facts that are missing today (date, OS/shell, cwd, git snapshot), added to the existing user-turn blocks.
- **Harness:**
  - verify-or-revert;
  - claim-vs-diff;
  - the loop breaker (#577, #583, #586);
  - a provider-error retry with backoff;
  - the owner identity reaching `solve` (defect 1);
  - the action nudge not firing on a turn that asked the permitted questions (defect 5).
- **Measure:** LoopsBench and Terminal-Bench, plus the discriminating SWE-bench subset *only if the owner funds it*; that phase is closed at US$ 0.
  - Primary: success and cost per success.
  - Secondary: the claim-vs-diff false-success rate.

### S2 — desktop Code screen (and S3 — terminal `chat`, `assist`, TUI)
- **L1 (`interactive`):**
  - The directive boundary (L0), made concrete: explaining, reviewing and diagnosing produce an answer; a change request produces the change.
  - Steering: a message sent mid-task refines the task and does not replace it, and a status question is not "stop".
  - Progress lines only when they change what the person understands: a discovery, a trade-off, a blocker, an edit about to start.
  - The final answer stands alone.
  - Never imply that work continues without a tool call in the same turn.
- **L3 surface:**
  - Desktop: markdown and file links.
  - CLI: plain text, `path:line`, short.
- **Context message:** recalled facts, the image note, jobs, works, the approved plan and retrieved skills, all moved *out* of the system prefix. This is the largest cache fix in the product.
- **Chat and Discord history:**
  - Today `ChatSession` flattens the last six turns into one user message (`interface/session.py`), so earlier tool calls are lost and nothing is cacheable.
  - Moving to real message history is a separate, measured change: cache hit rate plus a sharded multi-turn task set (2505.06120 protocol).
- **Measure:**
  - A new directive-boundary corpus (H1).
  - Recap on/off for long sessions (H7).

### S4 — voice (talk model + work model)
- **Talk L1, at most ~150 words:**
  - Gist first.
  - Two to four short spoken sentences (said as what to do, not five "no"s — 2601.08070).
  - Numbers, ids, emails and URLs in spoken form, or left to a TTS normaliser.
  - The owner's language, pinned.
  - An imperfect transcript is repaired without inventing a task.
  - Anything structured is handed to the work model, and the talk model says where to look.
  - Only completed actions and verified facts are spoken.
  - The worker is never hidden.
- **Work L1:** every message to the talk model starts with STATUS (discovery / transition / blocker) or COMPLETE (outcome / terminal limitation / exactly one question). Exact material is shown on screen, not spoken.
- **Harness:** the talk/work split stays deterministic (the regex, then a typed decision if one is measured to be better). Time to first audio is recorded.
- **Measure:** a deterministic spoken-format checker over recorded turns (no markdown, sentence count, digits spoken), plus time to first audio (H9).

### S5 — background works
- **L1 (`unattended`):**
  - Same as S1, without the coding block unless the work is code.
  - A machine-readable last line: `done`, `needs-input` or `failed`, which the UI parses.
  - "Done" means the owner's goal, not the current step.
- **Harness:** the status line is parsed and shown. A work that ends without one is marked `failed`, never `done`.
- **Measure:** long-horizon tasks with pauses; premature-stop rate; cost per success.

### S6 — hierarchy, crew, kanban, sub-agents
- **Workers (L0 + `worker`):**
  - The brief contract:
    - the owner's words by reference;
    - an explicit scope and an "only this" line;
    - the reply shape;
    - whether the worker may write, verify or spawn (default: no spawning);
    - a turn and budget cap.
  - You are not alone, so do not revert others' edits.
  - Report for the relayer, not for the owner.
  - The crew's "approaches" become a paragraph *inside* the worker module instead of the whole system prompt.
- **Orchestrator:** no edit tools. The task store is the truth over a worker's report. Waves are planned from file overlap, and sequential work is not parallelised (2512.08296).
- **Harness:** L0 is mandatory (defect 2). The synthesis step keeps the owner block last and uses the single language rule (defect 4).
- **Measure:** `bench/hierarchy_equal_calls`, `hierarchy_multistep`, `manager_diff`, and MAST-style failure counts.

### S7 — fusion (panel, judge, synthesiser)
- **Panel:** the situation module *without* tool language (defect 5). Diversity comes from a seeded angle per panellist, tested against the measured 1.46 effective votes (`panel_correlation`).
- **Judge:**
  - Blind, as shipped.
  - The candidate order is shuffled and the permutation recorded in the receipt.
  - It checks premises against the evidence it is given before comparing prose.
  - Typed output.
  - No "impartial" adjectives (2311.10054).
  - The agent's system text is fenced when it is passed in.
- **Synthesiser:** attributes claims to candidates and flags any claim no candidate supports. No new facts. The single language rule.
- **Measure:** the `judge_blind` hard corpus (one the judge cannot solve alone, to avoid the 169/240 re-derivation ceiling); panel correlation.

### S8 — verifiers and graders
- **L1 (`grader`):**
  - A three-state verdict — confirmed / plausible / refuted — that quotes the line.
  - The burden of proof is on the expectation.
  - An independent oracle: a check built from the hypothesis proves nothing.
  - A failing test is never narrowed.
  - One abstention semantics everywhere: "cannot tell" is `None`, never 0.0 (defect 6).
- **Measure:** `blind_audit`, `false_success`, `spec_test_vacuity`, and κ rather than raw agreement.

### S9 — governance judge and typed decisions
- **The instrument stays pinned.** `JUDGE_TEXT` is byte-fixed to its bench, and any change is an arm, not an edit.
- **Candidate arms:**
  - (a) a trust ladder plus "judge the capability delivered, not the label", against the #488 framing-wrapper corpus;
  - (b) one output schema for the hosted variant (defect 5);
  - (c) reason-then-verdict against verdict-first, read with the shipped calibration. First-token logprob decisions need an unambiguous first token, which ties to the JevBench collision finding (#551).
- **Harness:** a typed `blocked` outcome. Re-encoding a blocked command counts as a new action. The agent may never edit its own approval configuration (CVE-2025-53773).
- **Measure:** `governance_judge`, the perturbation, paraphrase and framing floors, and benign over-refusal (H6).

### S10 — always-on production agent (Discord persona + cron)
- **Discord (`channel` + `persona`):**
  - The owner identity reaches the bot (defect 1).
  - Trust tags per message: owner, other member, webhook.
  - Never mirror a thread's tone.
  - Never present the owner's opinions to third parties.
  - Constrained-format bait (one word, yes/no, acrostic) about contested topics is answered normally.
  - Per-message length under the platform cap, Discord markdown, no local paths.
  - A non-technical register unless asked.
  - Numbers from live, deterministic scripts only (the rule that already exists for eToro).
  - Memory used invisibly.
- **Cron (`steward`):**
  - A saved job states the task, not a recipe.
  - Runs are non-interactive: no questions, because nobody answers.
  - Stay silent when nothing changed.
  - Delivery is explicit, and an acknowledgement is not delivery.
  - Never repeat a prior run.
  - Preflight connectors with a harmless read.
  - The trading mandate comes from a policy lookup, not pasted numbers.
- **Harness:** a turn that produced a result must end in a delivered message. Webhook payloads are fenced (defect 3).
- **Production content** (job texts, workspace `AGENTS.md`) is rewritten and audited in the private ops repo.
- **Measure:**
  - cron traces: cache hit rate, stop reasons, cost per run;
  - messages without new information per day;
  - a drift probe on long persona threads (H12).

### S11 — browser and computer use (no module today)
- **L1:**
  - Take the cheapest state check, not both.
  - Act on the visible page.
  - Never re-open the current URL.
  - Try one direct URL, then one search fallback; no guessing loops.
  - Trust one authoritative success signal.
  - Stop at login, 2FA, CAPTCHA or payment and hand over to the person.
  - Never read cookies, storage or password stores.
  - Typing sensitive data into a page counts as transmitting it.
- **Confirmations:** one taxonomy with four classes, mapped to the owner's mandate list:
  - hand over;
  - always confirm at action time;
  - pre-approval in the request is enough;
  - none.
- **Harness:** the confirmations are governance gates, not sentences. The taint cards (#589) already do part of this.
- **Measure:** `browser_taint_cards`, `browser_element_list`, and an AgentDojo/WASP subset for utility under attack.

### S12 — explore and research sub-agents
- **Explorer:**
  - Read-only.
  - A thoroughness level (quick / medium / thorough).
  - `path:line` for every claim.
  - A gaps section separating verified from inferred.
  - It reports conclusions, not file dumps.
- **Web research (new):**
  - Search budget scaled to the question.
  - Rule out alternatives, not just support one.
  - Citations next to the claim, and ids only from tool output.
  - Dates resolved and labelled.
  - Recognising a name is not knowing its current state.
- **Measure:** a BrowseComp-style set (to build), source verification rate, tokens per answer.

### S13 — memory extraction and compaction
- **Extraction (new, after the turn, model-based):**
  - Origin rules: the user stated it, not the assistant's suggestion.
  - "Asked X in task T" is not a preference.
  - Plain facts, never imperatives.
  - A horizon test: still true and useful in a month.
  - Typed operations, with typed skip reasons (duplicate, temporary, secret, self-referential, belongs in docs).
  - Prefer saving nothing.
- **Recall:** into the context message, quoted, with source and date. Not proof of the present.
- **Compaction:**
  - Fix the trigger first (0/137).
  - Mask old observations before summarising.
  - Keep the measured rule-form summariser.
  - Add the verbatim fields: the user's messages, standing constraints, promises, ids, and errors quoted exactly.
  - Raise an alarm when the retained rule set shrinks sharply between compactions (2510.04618).
- **Measure:** `bench/compaction`, `memory_poison`, LongMemEval (not LoCoMo, whose answer key has a 6.4% error rate).

### S14 — plan mode
- **L1:**
  - The mode is a tag on every message and cannot be changed by the person's tone.
  - Non-mutating work only; cache-only builds and tests are allowed.
  - Explore facts before asking.
  - Ask preferences early, with 2–4 options and a recommended default. An unanswered question becomes a recorded assumption.
  - One decision-complete plan block, replaced on revision.
  - No "should I proceed?".
  - No invented v1 policy.
  - The planner prompt and the plan-gate prompt merge into one (they are nearly identical today).
- **Harness:** mutations are blocked while the mode is on. Cline needed a blocklist after prompt-only rules failed.
- **Measure:** plan-to-execution success, and an alternatives on/off arm.

### S15 — code review (no user-facing prompt today)
- **L1:**
  - Findings first, ordered P0–P3, each with `file:line`, the evidence and the consequence.
  - Only issues the change introduced, that its author would fix, and whose impact is shown.
  - An explicit "no findings", with residual risks and untested paths.
  - A strict JSON form for tools.
  - Finders report with confidence; a separate verifier filters. The finder is never told "only important".
  - A reviewer from a different model family than the author.
- **Measure:** `bench/review_judge` (recall 15.1% today, with its own prompt) and SWR-Bench.

## 8. Measurement protocol

This protocol applies to every behavioural change in §9. It is the protocol already written in `docs/benchmarks.md` and the study plans, made specific to prompts.

1. **Floors first, in the same session.** Measure each floor before any comparison: replay, paraphrase, framing, endpoint and cache.
   - Paraphrase: 3–5 neutral rewrites of the *old* prompt. Their spread is the smallest effect worth claiming.
   - Endpoint: an OpenRouter slug is a pool, so pin it.
2. **Paired design.** Both prompts run on the same items, with the same pinned endpoint, batch, temperature and cache state. Test with exact McNemar and a Newcombe interval, with standard errors clustered by repository.
3. **Honest n.** Paired items needed at α=0.05 and power 0.8, where p_d is the fraction of items on which the two prompts disagree:

   | Effect | p_d | Paired items |
   |---:|---:|---:|
   | 10 pp | 0.20 | ≈160 |
   | 10 pp | 0.30 | ≈235 |
   | 5 pp | 0.15 | ≈470 |
   | 5 pp | 0.25 | ≈785 |

   - With LoopsBench's 25% flip floor, a 50-task agent bench resolves nothing under about 20 pp.
   - Hypotheses are therefore chosen for large effects, or for deterministic metrics (a lint, a checker, a count) that have no sampling floor.
4. **Spread tasks before adding replicas.** At ICC 0.706, three replicas buy about 1.24 observations.
5. **Two-sided outcomes, always reported together:**
   - success;
   - cost per success;
   - tokens;
   - tool calls, over-calls and under-calls;
   - benign over-refusal;
   - time to first token, or time to first audio for voice;
   - cache hit rate.

   A +2 pp gain at 3× the cost is a regression for S5 and S10.
6. **Per model, factorial.** Prompt × model family × effort interact, and compound changes can be super-additive (−12.2 pp).
7. **The instrument must be able to show the effect.** Register a positive control. Check for ceilings (a judge that re-derives answers) and floors (0/382).
8. **Security arms need an adaptive attacker.** A static attack-success rate is not evidence.
9. **Pre-registration in the PR that runs the bench**, before the first call:
   - hypothesis;
   - arms;
   - primary metric;
   - floors;
   - n;
   - stop rule;
   - positive control;
   - adoption rule.

   Default adoption rule: the paired lower bound is ≥ −2 pp on success, and cost per success rises ≤ 20%. A simplification (removing text) is adopted on non-inferiority.
10. **Nulls are published.** They go into the study notes and the registry's status field.

**Literature grades** (from the literature agent): A = replicated or meta-analysis, B = single controlled study, C = benchmark without noise control, D = vendor or practitioner claim, E = opinion.

## 9. The work, in waves (one PR per item)

**Wave 0 — the registry, with no behaviour change**
1. `chimera/prompts/` registry plus snapshots. Every existing prompt moves in, and the snapshots must equal today's strings byte for byte.
2. Lint, report-only at first. It prints today's violations: five fence syntaxes, two language rules, CAPS density, tools named in prose.
3. The prompt snapshot hash in every receipt.

**Wave 1 — defects.** Each is a fix with a test that fails before it.
1. The owner identity reaches every owner-facing surface, or the Settings text stops promising it. Messaging bots and CLI `solve` come first.
2. L0 is mandatory in every worker prompt: hierarchy, sub-agent, explorer, crew.
3. One fence syntax, one sentence, and the webhook payload fenced.
4. One language rule and one precedence rule. The hierarchy synthesiser and `RoleAgent` put the owner last.
5. The contradictions: the ask exception versus the nudge, tool language in the fusion panel, and one schema for hosted governance.
6. One grader abstention semantics: `verifier_select` returns `None`, not 0.0.
7. The environment facts (date, OS/shell, cwd, git snapshot) in the context message.
8. The false comments and promises: skills the agent cannot call, the "reliably-stable" system message, the Settings text.

**Wave 2 — stable prefix, volatile tail**
1. The Code screen, chat and Discord move volatile content into the context message.
   - Measured: cache hit rate and cost per turn.
   - Success parity on the situation benches, reported as a separate outcome, because caching changes trajectories.

**Wave 3 — behavioural arms,** each pre-registered and each in its own PR.

| Id | Hypothesis | Situations | Primary metric | Notes |
|---|---|---|---|---|
| H1 | The directive boundary cuts unrequested writes on question, review and diagnose requests without lowering completion on change requests | S2, S3, S10 | Unrequested-write rate (deterministic: the file system diff) | New corpus: a pilot of 20 items to estimate p_d, then n from the table |
| H2 | "Every progress claim cites a tool result" cuts false success | S1, S5 | claim-vs-diff false-success rate | Anthropic reports this measured; we re-measure on our models |
| H3 | Last-paragraph check plus "blocked after 3 identical turns" cuts premature stops | S1, S5 | Stop-with-a-promise rate; success | Ties to the auto-continue work |
| H4 | A de-capitalised, reason-attached L0 is non-inferior and cheaper | all | Success (non-inferiority), tokens | Per family (§6) |
| H5 | Vendor temperatures per family beat the global 0.2 | S1, S7 | Success on a pinned endpoint | Cheap; one family at a time |
| H6 | The trust ladder plus a capability clause cuts framing-wrapper flips | S9 | Flips on the #488 corpus; benign over-refusal | The judge stays pinned; the change is a second arm |
| H7 | A recap before the final answer helps long sessions | S2, S3, S10 | Sharded multi-turn success | 2505.06120 protocol |
| H8 | Masking old observations matches the summary at lower cost | S13, S1 | Cost per success; success | Only after the trigger is fixed |
| H9 | STATUS/COMPLETE plus the spoken standard cuts unspeakable output | S4 | Spoken-format checker; time to first audio | Deterministic checker |
| H10 | The brief contract cuts invented requirements in worker output | S6 | Manager or diff audit of worker output | |
| H11 | The review rubric raises precision without collapsing recall | S15 | `review_judge` precision and recall | Recall of *correct* comments 92.4% today, out of sample (the 15.1% first written here was the pilot's rejection recall on incorrect ones) |
| H12 | The steward contract cuts no-information Discord messages | S10 | Messages with no new information per day | Run in ops, owner's call |

**Wave 3, as run (2026-09-25).** Every arm was pre-registered before its first paid call, and every outcome was published, including those that were not adopted. Total spend: about US$ 3.

*Decided arms.* No sentence was adopted, because none met its registered bar:

| Arm | Outcome | PR |
|---|---|---|
| H1 | Unrequested writes fell from 18/40 to 10/40 (p = 0.008), but the registered bar was "at most half", so it missed by one pair. The effect was on diagnoses (8 → 3), not on reports of a problem (8 → 7). | #610 |
| H6 | Framing flips fell from 27/94 to 18/94 (p = 0.049), which is not half, and it made 3 benign actions stricter against a limit of 2. The effect was on the easy corpus only. | #611 |
| H9 | The rewrite that names no forbidden form brought the Markdown back (format −23.6 pp, p = 0.0002). `SPOKEN_NOTE` stays. | #614 |
| H11 | Not run, US$ 0. Arm C, out of sample, already gave +4.5 pp precision for −35.9 pp recall of correct comments. | #609 |

*Uninformative arms.* The control never showed the failure this model was meant to fix, so there was nothing to test against:

| Arm | Why it stopped at its pilot gate | PR |
|---|---|---|
| H2 + H3 | False success 0/34, premature stop 1/34 | #617 |
| H7 | FULL 93.8% against SHARDED 90.6% | #616 |
| H10 | The manager invented a requirement 1/20 times | #613 |

*Arms not run:*
- **H4 and H5** need a bench that discriminates. The corpora built here sat at ceiling, and LoopsBench costs about US$ 25 a run, which is the owner's call.
- **H8** still measures a path production does not take (see below).
- **H12** is operations on the VPS, and the owner's call.

*What the arms found in the harness,* each fixed in its own PR:
- The blocking gateway paths dropped `thinking=` (#615, found by H9).
- An empty closing reply at the step limit is now asked again (#619, found by H1, H2 + H3 and H10).
- The compaction budget is now capped at the context the default model was measured to read well (#620): 128k, from `bench/useful_context` (#618), which moves the trigger from 503k to 102k.
- The ollama stream test flake, which LiteLLM 1.102.1 exposed (#612).

**Wave 4 — new modules**
1. S11 browser.
2. S12 web research.
3. S13 model-side memory extraction.
4. S15 user-facing `/review`.

Each ships behind a flag, with the bench named in §7.

**Cost.** Wave 0, Wave 1 and every deterministic metric cost US$ 0. Arms that call paid models are budgeted per PR and need the owner's go-ahead; the SWE-bench phase stays closed unless it is funded.

## 10. What we will not do

- Copy any captured vendor prompt text.
- Hide the worker behind the voice layer, or make any layer "never refuse".
- Use CAPS threats, tips, emotional appeals or competence personas.
- Put a tool router in front of the executor (#537), or defer our 28 tool schemas.
- Tell reasoning models to think step by step, compare approaches, or be certain.
- Patch a prompt sentence to pass one eval case. Fixes are general, or they live in the harness.
- Add a secrecy clause. Chimera is open source, and every prompt should read well if published.
- Let the agent edit its own approval or governance configuration. Reusable approval rules proposed by the agent are shown as suggestions, never auto-applied.
- Treat the prompt as the security boundary.

## 11. What "better" means, concretely

No vendor in the captures publishes a measured effect for its own prompt text. Aider's leaderboard and Gemini CLI's eval gate come closest. Chimera can be better in the ways that can be checked:

| Property | Best seen in the sources | Chimera target |
|---|---|---|
| Size of the prompt prose | Anthropic claude.ai ~85k tokens; Codex GPT-6 ~97k; Grok Bot ~55k; Pi ~390 words | L0 + L1 + L2 + L3 ≤ ~800 words per situation, measured per surface |
| Cache-stable prefix | OpenHands, Goose | Every surface; cache hit rate in every receipt |
| Every module's evidence on record | None; Gemini's eval gate is the closest | Registry status per section: measured / null / unmeasured |
| Rendered-prompt snapshots and lint | OpenHands snapshots; no vendor lints contradictions | Both, in CI |
| Honesty enforced outside the prompt | Cline's typed `verified`; Codex's completion audit | claim-vs-diff, verify-or-revert, typed finish |
| Coverage of situations | Each vendor covers a few surfaces | All fifteen, from one stack |
| Injection | Fences everywhere, all broken by adaptive attacks | Fence plus datamarking as defence in depth; capability and taint as the boundary |
| Per-model tuning | OpenCode, Cline, Codex | Overlays as data, each field measured |

Each row is either a property of the code, which a test can check, or a number, which a bench can report. None of them is an adjective.
