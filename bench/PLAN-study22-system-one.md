# Study 22 — System One in Chimera: what the evidence allows, and the architecture that follows

*2026-09-23. Asked by Bruno after B4 closed (#537): study TypeSafe's "System One" decision models end to end — the
full documentation, the papers, the open imitations, the ecosystem integrations — read Chimera's own code, prompts and
benches against them, and design how to implement the idea in the terminal agent, the desktop app and skills.
US$ 0. Three research fronts ran in parallel; their raw reports are summarised here with every source kept.*

**Sources read.** 61 of 62 TypeSafe documentation pages (the OpenRouter model page 404'd twice; its endpoints JSON and
the OpenRouter guide were read instead), plus 18 JS API sub-pages, the launch blog, the manifesto, the Master
Customer Agreement (current and archived), TypeSafe's MIT repos (`typesafe-sdk-python`, `system-one-adapter-python`,
`skills`), OpenRouter's Decisions API and Jev Lab. 13 arXiv papers from September 2026 in full HTML (the five Bruno
listed plus eight more found by search) and ~40 references followed from them. The `laya` repository in full.
LangChain, Vercel AI Gateway, Cloudflare Workers AI and Pydantic AI integration docs, Meta's asset-classification
case study, and ~14 open decision/judge models on HuggingFace/GitHub. Both X posts were unreachable (HTTP 402; nitter
refused) — the author's Substack essay on the same subject was read instead. Chimera: every module that makes a
bounded judgment, every prompt that exists only to obtain one, every bench that measured one, and the label data on
the owner's machine.

---

## 1. The verdict in one paragraph

**Chimera already has the System One interface** — `chimera/decisions/` is Noul/Choice/Score → distribution,
calibrated per (decision, backend, model, instrument, build), with a receipt, and a halt is never a verdict. What it
lacks is (a) a **rule about which decisions a fast model may make**, (b) a **label loop** that turns human answers
and oracle outcomes into refitted maps, and (c) **surfaces** beyond the governance band. The literature and TypeSafe's
own documentation agree with every null this project measured, and they agree for the same reason: *a typed decision
is reliable on a short, factual state with a bounded answer, and unreliable when it judges prose, claims, relevance,
or whether the work is done.* So the architecture is not "put a fast model in the loop". It is **a decision layer that
may only add scrutiny** — review, a nudge, another verification, a bigger model — backed by a label loop that makes
every threshold a measured one, exposed as an open System One interface that Chimera's own agent and its users can
call.

---

## 2. Eight design invariants, each with the evidence that forces it

| # | Invariant | Our evidence | External evidence |
|---|---|---|---|
| **I1** | **Direction rule — a decision may escalate, never de-escalate.** It may raise REVIEW, nudge, trigger a verification, move to a bigger model, annotate. It may not stop the loop, remove tools, skip verification, accept work, or suppress a human card. | B4 (#537): a router that could say `ANSWER` cost −0.087 / −0.194 / −0.307 oracle score, worse the stronger the executor; `ANSWER` share tracked the damage. | REFLEX (arXiv 2609.26532): Jev picks the right function 98.4%, decides *whether to act* 52%. TypeSafe never lets Jev control an agent loop; its skill-suggestion cookbook injects a soft hint the agent may ignore, recall-biased at 0.30. Jev-Mem (2609.23986): stopping works only as several signals AND hard budgets. Evidence-carrying termination (2608.23623): unsupported stops 0/66 vs 40/66. |
| **I2** | **The state is the thing being judged, alone.** No tool output, no assistant prose, no framing sentence, no "none/no" facts, no batching of states. | facts_in_state: a column of "none" lowered *p* everywhere, 50→46 OATS. perturbation_floor §8: one unverifiable sentence moved 8–9/14 ambiguous attacks to ALLOW. Tier B2: batching 10 states moved *p* by 0.275. | TypeSafe jaggedness page: large irrelevant state and self-advocating content move answers; Jev does not treat state as hostile. LangChain's guard excludes tool output and assistant messages from the classifier. JEVQA (2609.24395): one unit normalization in the state moved PLCC by ~+0.2. |
| **I3** | **Neutral option identifiers; the meaning lives in the rubric.** Never "yes/no", "ANSWER", "safe" as the label the model reads. | Tier B1: reversing option order flipped 5/55 on the local arm. | "Type-Safe Is Not Error-Free" (2609.26758): swapping rubrics under yes/no labels changes 76.9% of answers and inverts AUROC 0.94→0.23; under neutral 0/1 labels, 6.5%. |
| **I4** | **Decision-first, one read, no reasoning before the label.** | Local L2 arm: reading after the trace dropped AUROC 0.871→0.782, *p* collapsed to 0/1, 51 s/item. | Kumaran (2606.29490): verbal confidence tracks commitment, not correctness; post-reasoning logprob saturates. Visual Jev (2609.25845): special heads gave nothing over the LM head. |
| **I5** | **Only a calibrated *p* crosses a threshold; the map is per question, pooled when data is thin.** Read the Noul, never a raw Choice mass or a vendor "confidence". | Study 21: Choice mass +0.11 above the Noul, ECE 0.221 vs 0.120. Jev over-confident mid-scale (stated 0.60 right 27%). | Crash narratives (2609.24052): slopes 0.51–1.24 per variable, recalibration cuts ECE 3.3×; pool families with <20 positives. TypeSafe: calibration is group-level, thresholds are domain-specific; confidence is a shape statistic, (K·p_max−1)/(K−1) fits every published example. |
| **I6** | **Decompose, then combine in code.** Many atomic questions with named weights beat one holistic judgment. | claim-vs-diff as one Noul: AUROC 0.537 (= shuffled control). aacr review findings: ECE 0.405. | TypeSafe's spam (6 atomic Nouls), overseer (4), SDE cascade (7), composite scoring; beri write-up: phishing 62.6%→95% by decomposition. Link (Zenodo): *absence* is detected at 0.31 vs 0.99 for explicit statements — never ask "is anything missing?". |
| **I7** | **Fuse, don't replace.** A decision reorders or filters what a deterministic stage produced; it does not become the ranking. | rag_rerank: Noul-per-chunk replaced RRF and lost −7.8 pp recall@10. | Jev-Mem fuses Jev relevance with embedding rank; TypeSafe's rerank cookbook reranks BM25 top-30 and says the shortlist bounds recall. |
| **I8** | **Every surface is shadowed before it acts, earns an AST `ALLOWED` entry with a bench, and fails toward scrutiny.** An unreadable or halted decision means "uncertain → escalate", never "pass". | Three quality judges fail *open* by design (spot check, strong verifier, checklist grade) — an outage must not block a run — but they recorded the outage as a verdict (see §6, phase 0 as done). The AST guard in `tests/test_a_decision_has_a_contract.py` already enforces the bench-first rule. | Meta: rules first, model on the gray zone, shadow mode, promote by compare-and-swap, versioned context. "Doomed from the start" (2607.06503): recall-controlled abort gates keep 90–95% of success. |

---

## 3. Where Chimera is today (from the code map)

- **Contract** `chimera/decisions/` — complete for one state × one question. Backends: `local_logprob` (Ollama
  qwen3:4b, JSON-enum, label read from the end, the default), `hosted_verbalized`, `openrouter_decisions` (Jev, pinned
  `typesafe/jev-1.13`). Shipped map: `governance.danger` on qwen3:4b@Q4_K_M. Refits read from
  `<home>/decisions/maps.json`. AST guard `ALLOWED = {chimera/governance/band.py}`.
- **Consumer** — the kernel REVIEW band (`governance/band.py`), off by default (`CHIMERA_GOVERNANCE_BAND`).
- **Label join** — built: `approvals/history.jsonl` stores *p*, band, build beside the human answer.
  **Nothing fits a map from it** (the only caller of `PlattMap.fit` is `bench/jev_decisions/fit_map.py`).
- **Label reality on the owner's machine** — 197 approval rows, **all raised by taint narrowing, none carrying *p***,
  99.4% approved: they measure approval fatigue, not danger. `observe` mode produces no human labels at all.
- **Bounded judgments made by generative calls with no number**: Manager APPROVED/REVISE, strong verifier (0–10),
  checklist grade, progress ledger, envelope spot check, fusion selector, rubric, tool router. Three fail open by
  design, and until phase 0 wrote the abstention down as a pass.
- **Evidence** (all pre-registered): works — governance danger on short action states. Fails — claim-vs-diff,
  review findings, relevance reranking, facts-in-state, the stop decision.

---

## 4. The architecture — three layers, built in this order

### Architecture A — the decision layer v2 (in-process, the core)

```
            ┌──────────────────────── chimera/decisions/ ────────────────────────┐
 surface →  │ DecisionSpec (registry)  →  Asker (fan-out, cache)  →  Backend      │
 (kernel,   │   name, questions,          one state, N questions,     local_logprob│
  solve,    │   direction=ESCALATE,       isolated reads,             hosted       │
  voice,    │   thresholds, bench,        decision cache on           openrouter   │
  spot      │   mode=shadow|enforce       normalized state            (+ encoder)  │
  check…)   │        │                                                  │         │
            │        ▼                                                  ▼         │
            │   Policy (code) ◄──── Answer: p, calibrated, confidence, receipt    │
            │   maps p → {REVIEW, NUDGE, VERIFY, ESCALATE_MODEL, ANNOTATE}        │
            │        │                         │                                  │
            │        ▼                         ▼                                  │
            │   surface acts            DecisionLog (decisions.jsonl) ──► Label   │
            │   (only upward)           receipt + outcome slot            loop    │
            └───────────────────────────────────────────────────────────────────┘
```

1. **`DecisionSpec` registry** (`chimera/decisions/spec.py`, new). Every decision point is declared once: its
   questions, backend, thresholds, **`direction`** (a type with only escalating members — `REVIEW`, `NUDGE`,
   `VERIFY`, `ESCALATE_MODEL`, `ANNOTATE`; there is no `STOP`/`SKIP`/`ACCEPT` member to return), `mode`
   (`shadow` records only, `enforce` acts), the bench that justifies it and its calibration key. The AST guard moves
   from "who may build a Decider" to "every `DecisionSpec` names a bench file that exists" — I1 and I8 become
   impossible to violate by accident.
2. **Fan-out asker.** One state, N questions, each read in isolation (TypeSafe's "speculative fan-out"). On the local
   backend the state prefix is shared through Ollama's KV cache, so N questions cost ~one prefill + N short reads;
   on hosted backends isolation means N calls, and the spec says so. **A decision cache** keyed on
   (normalized state, instrument hash, build) — repeated shell commands are the common case (edge-orchestration
   paper: caching erased the latency gap entirely).
   *Correction, 2026-09-23 (phase 1):* the shipped instrument puts the state **after** each question's
   instructions, so N questions share no prefix and cost N full reads (measured 3.92× for four).
   State-first would share it, but it is a different instrument — a new hash that the shipped
   governance map does not cover — so it waits for a bench of its own.
3. **Answer v2.** Adds `confidence` = (K·p_max−1)/(K−1) for Choice/Score (documented as a shape statistic, never
   thresholded — I5), `expectation` for Score, neutral-label rendering (I3: options sent as `0/1/2…` or `A/B/C` with
   the meaning in `criteria`), and a Meta-style trace: matched rule, instrument hash, map key, build, cache hit.
4. **Question linter** (`chimera/decisions/lint.py`, new) — TypeSafe's jaggedness list as static checks run in tests:
   no compound condition ("and/or" in a Noul), affirmative phrasing, instructions and criteria aligned, no numeric-only
   levels, no polar option names, a first-token collision check across options (study 21 A4, still missing), no
   reused threshold across primitives.
5. **Fail toward scrutiny — for new decisions.** `halt`, parse failure and "uncalibrated" all map to the spec's
   escalating default. The three existing judges (spot check, strong verifier, checklist) keep failing open: their
   docstrings choose availability on purpose, and turning a provider outage into a blocked run is a trade the owner
   makes, not a hygiene fix. What phase 0 removed is the lie: an abstention no longer reaches the record as a pass
   (`"spot"` in `checks_run`, a 10/10 grade, "all requirements met").
6. **DecisionLog** (`<home>/decisions/decisions.jsonl`) — every answer with its receipt and an empty `outcome` slot
   that the label loop fills.

### Architecture B — an open System One interface (the product surface)

The same layer, reachable from outside the process, in the request shape the ecosystem already speaks
(`{state, questions} → {answers: {noul | choice+probabilities+confidence | score+probabilities+legend+confidence}}`),
so code written against it ports to or from any System One backend.

| Surface | What it adds | Where |
|---|---|---|
| **Terminal** | `chimera decide` (ask typed questions over a state or over every line of a JSONL file — the map-reduce use case, local and free); `chimera decisions {list, report, refit, shadow}` (calibration report per key: reliability, ECE, review budget H(π*), drift) | `chimera/cli/` |
| **Desktop sidecar** | `POST /api/decide` in the SDK-compatible shape; a **Decisions screen** (per-key reliability diagram, review budget, shadow vs enforce, last 50 decisions with their outcomes); a **label affordance on the approval card separate from Approve** ("was this action actually dangerous?") — because consent is not the event | `chimera/api/`, `apps/desktop/src/components/` |
| **Agent tool (MCP + built-in)** | `decide` tool: the agent can classify, filter or verify many items cheaply inside its own work (triage 1,000 log lines, filter candidate files) — typed answers instead of prose it must parse | `chimera/tools/` |
| **Skill** | `skills/system-one-design/SKILL.md` — teaches the agent (and users) to turn a prompt-and-parse step into typed decisions: atomic questions, state hygiene, neutral labels, thresholds from data, direction rule. Adapted from TypeSafe's MIT skill with attribution | `skills/` |
| **VPS / Chimera production** | Opt-in; same service. Off by default, like governance there | — |

**Legal boundary (verified 2026-09-23).** TypeSafe's Master Customer Agreement was updated on 2026-09-19. The version
dated 2026-08-27 (Wayback, captured 09-16) forbade in §2.3(f) publishing "benchmarks or performance information";
**the current version no longer contains that clause** — study 20 and `bench/jev_decisions` read the old version
correctly at the time. The restriction that remains and matters is **§2.3(b)**: the Services or their Output may not
be used for model distillation, to train a model to imitate their output, or to develop a similar or competing
product. Therefore: Architecture B is designed from public documentation and our own labels only; **no backend,
prompt or map of ours is ever tuned against Jev answers**, and Jev stays an optional backend the user brings their own
key for. TypeSafe itself ships an MIT reimplementation of the interface over ordinary LLMs
(`system-one-adapter-python`), which is the public-domain precedent for the interface shape.

### Architecture C — our own trained decision backend (later, gated)

A LoRA + decision head on the qwen3:4b the default backend already runs (Luce-style, Apache-2.0), trained with
cross-entropy + Brier on **our own labelled items** (governance corpus, OATS, blind_audit, harness oracle outcomes,
reviewed voice rows), temperature-scaled, deployed as θ₀ + λΔ so λ=0 is an exact rollback (this-that paper). Optional
encoder backends (`laya`, `openJev-verdict-2.0`, Apache-2.0) and `Qwen3-Reranker` for relevance, all user-downloaded,
never vendored. **Gate:** only when the label loop has produced ≥300 labelled items for a decision family and the
trained head beats the calibrated logprob read on a held-out split. The this-that paper is the warning: a targeted
second training round transferred to 0 of 13 other families.

---

## 5. Decision points: adopt, shadow, or never

| Decision point | Where | Verdict | Why |
|---|---|---|---|
| Shell action danger (REVIEW band) | `governance/band.py` | **Adopt — roll out** | The one measured success; add the overseer battery (off-task / could-destroy / untrusted-input / ask-first as four atomic Nouls, I6) as a shadow arm and measure it against the single question. *Measured 2026-09-23 (`bench/overseer_battery`, pre-registered): the battery is **worse** — ΔAUROC −0.319 [−0.480, −0.155]; "destroys" is near-orthogonal to a label that pairs destructive-but-legitimate cleanups with exfiltration attacks. No shadow arm; the band reads the single question.* |
| Envelope spot check (DROPPED) | `orchestration/envelope_verify.py` | **Adopt, shadow first** — *not with a local 4B* | blind_audit: 69 envelopes with known truth; DROPPED-only already 23/23; today it fails open. *Measured 2026-09-23 (`bench/spot_noul`, pre-registered): a local qwen3:4b Noul answers "no" on all 92 envelopes, AUROC 0.655 [0.538, 0.759] — null; the spot check keeps the hosted auditor.* |
| Voice talk vs work (gray zone only) | `api/code_api.py` `_is_work` | **Shadow → adopt after 100 reviewed rows** | Regex decides the clear cases; the Noul only sees the ambiguous ones (Meta's 85/15) |
| Manager P(approved) | `core/supervisor.py` | **Record-only, forever unless benched** — *benched: null* | 36% of claimed successes are false; a number beside the feedback feeds PROBE, never gates. *Measured 2026-09-23 (`bench/manager_p`, pre-registered, US$ 0.064): a local Noul reads AUROC 0.644 [0.476, 0.780] overall and **0.525 within task** — it recognises hard tasks, not bad answers; not shipped. The Manager shown prose only approved 6/385 (it asks for the artifacts it cannot see); the diff path it has in production is untested.* *With the diff (`bench/manager_diff`, US$ 0.10): TPR 0.19 / FPR 0.01, TPR−FPR 0.177 [0.087, 0.268] — a precise, very strict gate that rejects ~3 in 4 true successes over the worker's account of itself; 123/385 rows had no visible change because the reconstruction cannot see edits under `in/`.* |
| Narrate-instead-of-act | `core/agent.py` `insist_on_action` | **Adopt (nudge only)** | It can only add a nudge |
| Fuse this turn | `fusion/router.py` | **Shadow; needs a corpus** | Escalating by nature (more models), cost is the only risk |
| Skill/card relevance | `evolution/card_retrieval.py` | **Shadow; per-card Noul, may only remove** | TypeSafe's own skill cookbook: wrong loads 16.8%→7.3%, and it broke 7 cases |
| Strong verifier trigger | `core/autonomous.py` D9 | **Change the gate, not the signal** | verifier_by_uncertainty: fired 0/385 |
| RAG relevance | `rag/hybrid.py` | **Only fused with RRF; benchmark vs Qwen3-Reranker first** | I7 |
| **Tool router / `ANSWER`** | `core/tool_router.py` | **Closed.** Never as a gate (B4: −0.087 / −0.194 / −0.307); as a hint, no effect (B4b: +0.063 / +0.003 / +0.008, all inside the floor, hint followed on 31–36% of steps). Both modes stay opt-in for reproducibility | B4, B4b; I1, I2, I3 |
| **Any "is it done?" / auto-accept / skip-verification** | ledger `complete`, auto-continue, cascade skip-climb | **Never** | I1 |
| **Auto-approving taint reviews** | `governance/ledger_tool.py` | **Never** | This is exactly where framing attacks live |
| Claim-vs-diff, review findings, memory gate by model | — | **Never** (measured) | AUROC 0.54, ECE 0.405, memory_poison |
| `classify_task`, loop breaker, stop counters, diff gate, verifier, taint | — | **Stay deterministic** | They read machine facts |

---

## 6. Roadmap — each phase ends in a bench, not a feature

| Phase | Content | Gate | Cost |
|---|---|---|---|
| **0 — Hygiene** (done, 2026-09-23) | An abstention is never recorded as a verdict (spot check, strong verifier, checklist); zero label mass is no signal, not p = 0; stale "no surface wires a Decider" docstrings; `EXIT_AT` as a setting; A4 first-token collision check; correct the MCA statements in study 20 / jev_decisions (clause removed 2026-09-19) | Tests + sabotage | US$ 0 |
| **1 — Contract v2** (done, 2026-09-23) | `DecisionSpec` + escalate-only `Escalation` type + AST guard that every spec names a bench file on disk; `decide_many` (isolated reads — **no** shared prefix: state-first is a new instrument and would orphan the shipped map); `DecisionCache` keyed on the exact state; Answer v2 (`confidence` as a shape statistic, `cached` on the receipt, `Choice.neutral()` + `restore`); question linter (`chimera/decisions/lint.py`). Governance registered as the first spec | Contract tests + sabotage; `bench/decisions_v2/RESULTS.md`: 1 question 0.32 s, 4 questions 1.26 s (3.92×), cache hit 0.1 ms, 0 halts / 385 | US$ 0 |
| **2 — Label loop** (done, 2026-09-23) | `<home>/decisions/decisions.jsonl` (every answer with its UNROUNDED `raw_p`; outcomes as later lines, latest wins); the log id travels answer → verdict → question file → card → history; the card's optional *was this dangerous?* (separate from Approve — an approval is never read as a label) → `POST /api/decisions/{id}/label`; `chimera decisions log/label/report/refit` — refit pools with the shipped rows (now in the package, `SHIPPED_ROWS`) while a class has < 20 labels, only across the same instrument **and build**, writes only with `--write`; report gives the review budget (cards / 100 decisions) and label coverage by band region. *Deviations:* pooling is per decision instrument, not per attack family (deployment rows carry no family); the `verifier`/`oracle` outcome sources exist in the API but no surface fills them yet — the band is the only decision, and danger has no oracle | Refit on the bench rows through the log = the shipped map to 1e-9 (a 6-place rounding in the first draft of the log moved the slope in the 6th decimal and was removed); report on the owner's home: **empty** — the band is off there, so no decider has answered | US$ 0 |
| **3 — First surfaces, shadow → enforce** | ~~Band rollout with the overseer battery as a shadow arm~~ (measured worse, 2026-09-23, `bench/overseer_battery`); ~~spot check as Nouls on blind_audit~~ (local 4B: null, 2026-09-23, `bench/spot_noul`); voice gray zone once 100 rows are reviewed; ~~Manager *p* record-only~~ (null, 2026-09-23, `bench/manager_p`) | One pre-registered bench per surface; enforce only on a passed gate | ≤ US$ 1 (local) |
| **4 — Open interface** | `chimera decide`, `/api/decide`, Decisions screen, `decide` agent tool + MCP, `system-one-design` skill | Round-trip tests in the SDK shape; a map-reduce demo on 1,000 items, local, timed | US$ 0 |
| **5 — Backends** | Optional encoder/reranker backends; Luce-style LoRA head on our labels | ≥300 labels per family; beats calibrated logprob on held-out | local GPU |
| **6 — The stop question, properly** | REFLEX-style B4b: confident which-tool only, progress in state, neutral ids, executor keeps whether-to-act; evidence-bound stop (ECT) as a *verification* trigger | Pre-registered, same harness as B4, three executors | ~US$ 30 |

---

## 7. What this study cannot show (§2q)

- None of the September 2026 papers are peer-reviewed; most appeared within a week of Jev's launch, several by
  vendors or competitors (this-that is a competitor; laya compares against third-party numbers it never measured and
  its benchmark's gold labels come from a ~4B teacher; its star count — 17k in four days — is not evidence).
- LangChain's "92–913× lower variance" is five runs and one reviewer; low variance is reliability, not validity.
- The inferred confidence formula matches every published example but is not documented.
- The X posts could not be read.
- Nothing here has been measured on Chimera beyond what §2 cites; every "adopt" above is a hypothesis with a named
  bench, not a result.
