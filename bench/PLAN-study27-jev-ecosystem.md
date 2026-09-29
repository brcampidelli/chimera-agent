# Study 27 — the Jev ecosystem, read, and what it adds to Chimera

*2026-09-28. Asked by Bruno: read the ecosystem list (awesome-jev, ~430 entries) plus ~25 named
repositories and the TypeSafe cookbooks end to end, read them against studies 20/22/24/25/26 and
Chimera's own code, and turn what survives into a phase plan in the study-22 format — each phase
ending in a bench, not a feature.*

**Sources read in full.** `docs.typesafe.ai` (llms.txt, use-case map, agent-skill page, cookbooks
`skill_suggestion`, `sde_cascade`, `autoresearch_feature_discovery`,
`classification_using_confidence`, demo `smart-home`); the ecosystem lists (`yibie/awesome-jev`,
`Anil-matcha/awesome-jev-by-typesafe`); and these repositories' READMEs and key source files:
`kerpopule/hermes-jev-skills`, `24601/Augustus`, `parkavenue9639/jevloop` (model.py + FastAPI case
study), `socai-io/jev-social`, `OpenByteInc/QuantDinger` (`ai_decision_filter.py`),
`AkashPriyadarshii/jev-superpowers`, `prasanthj/duckdb-jev`, `Thneoly/r2r-jev`,
`kylemclaren/jevsearch`, `browser-use/jev-ultrafast`, `chy4pro/jev-for-chrome`,
`valentynkit/jev-belay`, `valentynkit/jev-commit`, `valentynkit/jev.nvim`, `doeixd/jev-pref`,
`xinyao27/jevonian`, `saembit/jeff-cli`, `shitianfang/wakegate`, `shitianfang/jev-use`,
`supercorp-ai/supercov`, `aowang-ai/jev-trade`, `bartlomein/oko`, `steven-shoemaker/hunch`, and
the BTK SEO-audit cost study. Chimera re-read: `chimera/decisions/`, `governance/band.py`,
`scheduler/`, `core/context_budget.py`, `core/strong_verify.py`, `fusion/router.py`,
`rag/hybrid.py`, `evolution/card_retrieval.py`, `tools/decide.py`, the settings card and the
CHANGELOG entries for 0.63.x. **Cost so far: US$ 0 — no API call was made for this study.**

---

## 1. The verdict in one paragraph

Every measured null in studies 20–24 held in the ecosystem's own hands: the best third-party
projects fail toward scrutiny the same way ours does, and the failures the ecosystem published are
the failures we measured first (limpet's done-judged-from-wording, AUROC 0.50; hermes's handoff
*written* by Jev recalling less than the literal transcript; jevsearch's rerank converting recall
that retrieval, not judgement, bounds). What the ecosystem adds is not a new architecture — ours is
already the conservative one — but **specific mechanisms with published numbers that fit inside our
eight invariants**: an evidence gate before the stop question (jev-belay), a literal keep/drop
compaction (fast-jev-compaction, jev-compaction), a wake gate in front of the cron loop (wakegate),
per-field extraction cascades (SDE cookbook), coarse-when-unsure labelling
(classification_using_confidence), fused reranking over a shortlist (jevsearch, oko), a cost/rate
gate in front of the decider (decision-gate), and redaction discipline for what a hosted backend
sees (hermes-jev-skills). Each becomes one phase below, each phase ends in a pre-registered bench,
and **shadow is the default state of every one of them.**

## 2. What each phase will actually do

Every phase below names: the mechanism adopted, where it lands in the tree, the bench that gates it,
and the invariant it could break if we are careless. Nothing here re-opens a closed verdict: the
tool router stays closed (B4), auto-approval of taint reviews stays never, and a decision still
cannot say STOP/SKIP/ACCEPT — `Escalation` does not grow a member for any of this.

---

### Phase 0 — Hygiene carried forward, one new linter rule (US$ 0, no backend)

**What will be done:**

1. **Exit codes and outcome vocabulary for every new CLI surface.** Before any phase below ships a
   command, the CLI contract is fixed the way `jev-pref` and `jeff` do it: `0` accepted, `1`
   infrastructure/usage failure, and (for gate commands) `10`/`2` — a gate that fired or a policy
   failure — **never** collapsing exit 1 into "not approved". An infrastructure error is not a
   verdict; today `chimera decide` records a halt, but the exit code is the same for "answered" and
   "failed". This phase separates them, in code and in the docs, so CI can depend on the contract.
2. **Question-linter additions from the ecosystem's own failure pages.** Three static checks added
   to `chimera/decisions/lint.py`, each traced to a measured failure in the sources, not to taste:
   - **negated phrasing**: jev.nvim measured `noul(X) + noul(not X)` not summing to 1 and rewrote its
     guidance ("phrase positively"); the linter already prefers affirmative phrasing — this tightens
     it to reject a question whose predicate is a bare negation of its option's criterion.
   - **option-name collision across scripts**: jeff's `choice` takes option flags whose first token
     can collide; our A4 first-token check exists — the addition is that the linter now runs it over
     *the rendered criteria strings*, not only the option ids (the skill_suggestion cookbook ranks
     182 skills whose names share prefixes and survives only by putting the full description in the
     criterion).
   - **state-cap lint**: QuantDinger's filter raises when confidence is missing; the parallel
     hygiene rule here is that a question whose criteria exceed a byte budget is rejected before a
     call rather than silently truncated (duckdb-jev: oversized evidence fails without truncation).
3. **A compat test-suite for our own `/api/decide`.** `jevcompat` (48 requirements, each cited to
   the vendor's docs) found 2 of 8 open servers out of conformance. We write the subset that applies
   to us as round-trip tests: probabilities per option summing to 1, expectation = Σi·p, 2–255
   options, error shapes, answers stable under question reordering and id changes. Our server
   already passes all of it informally; the point is that it now **cannot regress silently**, and a
   third-party client written against the ecosystem wire format ports to us.

**Gate:** tests + sabotage only; the AST guard still holds `ALLOWED` at the existing surface set.
**Invariant served:** I8 (fail toward scrutiny — a malformed request never becomes a verdict).

---

### Phase 1 — Wake gate on the cron daemon (surface: `chimera/scheduler/`)

**What will be done:** wakegate, adapted to our daemon, the first surface because it is the one
place a typed decision saves money with **no** safety exposure at all.

1. A new `DecisionSpec` (`scheduler.wake`), mode **shadow**, escalation `ANNOTATE` — the only thing
   it may do is record that a dispatch was judged not worth a turn. It asks one `Choice` over
   `(wake, not_yet, unrelated)` — wakegate's dev-set winner, picked from three candidate wordings on
   a separate set — with the state built from: what the job is, its last result, whether the event
   is user-raised (never asked — always wakes), and the skip count so far.
2. The policy in code is wakegate's table, verbatim in direction: wake on `fromUser`, on any error
   or timeout, on p(wake) ≥ 0.5, on the unsure band (0.2–0.5), and on `skipped ≥ max_skips`; skip
   **only** on a confident p(wake) < 0.2 — and in shadow, nothing is skipped at all, the verdict is
   only written beside the dispatch line in `cron_results.jsonl`.
3. Because a skip suppresses a dispatch, the invariant question is whether this is de-escalation.
   It is not, and the spec records why: a skipped cron turn loses no verification, no review and no
   human card — the job simply runs on its next tick. The danger case (an agent sleeping through
   something that matters) is bounded by `max_skips` and by the user-message always-wake rule, both
   in code, not in the model.

**Bench (pre-registered before any scored dispatch):** hand-written scenarios over the owner's real
cron history (the jobs in `<home>/scheduler/jobs.json`), ≥30 cases split wake / not-yet, labelled
before any run; primary metric = skipped-that-should-have-woken (target 0, the wakegate table),
secondary = wakeups correctly suppressed and the model cost of the gate (their measured p50: ~250 ms,
one call). Kill criterion: any false skip on a user-raised event — that path must be unreachable by
construction and is asserted in tests, not measured.
**Invariant served:** I1 read narrowly — this decides spend, never scrutiny.
**Cost:** local backend only, ~US$ 0.

**RAN — 2026-09-29, verdict NULL** (`bench/wake_gate/RESULTS.md`, by the rule fixed in
`bench/wake_gate/PREREGISTRATION.md`): false skips **0** (the safety bar, at every swept floor
0.1–0.5) but suppression **0/17** against the ≥ 50% bar — the 4B model answered `wake` on 31 of 34
scored scenarios, including every `unrelated` webhook. AUROC 0.796 (CI 0.636–0.933), inside the
registered prediction band: the *probability* ranks, the *choice* never puts down. No shadow arm,
the daemon unchanged; the named successor candidate (a policy reading p_wake against a swept cut,
not the choice) is a new registration, not an amendment. The corpus (36 labelled scenarios over
the owner's real job) and the instrument stay.

---

### Phase 2 — The stop gate: "done" with no evidence (surface: `core/agent.py`, the turn end)

**What will be done:** jev-belay's hook, on our loop, and it is the phase where the study-22 §5 row
("any is-it-done / auto-accept — never") gets its constructive counterpart: the never was on
*accepting* work; this gate *adds a verification round*, which is `VERIFY` — the most escalatory
member of `Escalation`.

1. **Two regex belts run first, always free** (jev-belay's measured ablation is the reason the
   order matters): belt 1 reads the transcript slice for a test/build/lint runner named in a
   command (pytest, mypy, ruff, cargo, go, vitest, jest… — the shipped list plus whatever our
   project gate command emits, `make check`), belt 2 for the runner's own summary in the output.
   Files-changed-with-no-passing-check is computed in code.
2. **Only when that gate says "changed and unproven"** does the decider see one request, four
   questions in it, on the evidence-adjacent state (task, final message, counts — never diffs,
   never tool payloads, secret-shaped strings redacted by the existing scrubber): `claims_done`,
   `claims_verified`, `verification_applies` (their fourth question — the ablation's +0.09 AUROC arm),
   and `outcome` (complete / partial / blocked / other), where `blocked` vetoes a block.
3. **The action is `VERIFY`**, not a stop refusal: the agent gets one nudge — the run-facts reason in
   the ecosystem's own wording — and goes back to run the suite. It is capped the way jev-belay caps
   it (one per prompt, none within 60 s, three per session) because a wrong gate costs one extra
   test run, not a loop. Our `insist_on_action` already pushes back once on narrate-instead-of-act;
   this covers the sibling failure it does not see: narrate-instead-of-*verify*.
4. Ships **shadow** (`bench/stop_gate` logs would-have-blocked verdicts beside real run outcomes)
   and stays there until the bench below passes its gate.

**Bench (pre-registered):** the ablation in three arms on labelled stops from our own run corpus
(the harness transcripts already on the owner's machine), labels via the proxy-labeller rubric
pattern jev-belay publishes: arm 1 wording alone, arm 2 + evidence gate, arm 3 + `verification_applies`
(shipped). Primary: AUROC with bootstrap 95% CI; bar = beat wording-only by ≥ +0.08 with the CI's
lower end above +0.02 (their bar, stated because at n=100 with 12 positives the second decimal is
noise). Secondary: wrong-block rate ≤ 2% of stops at the chosen threshold; the threshold is
**swept**, not picked — the sweep prints blocks / caught / wrong per cutoff and the number chosen
carries the sweep beside it, the way the ecosystem's READMEs do.
**Invariant served:** I1 (the gate can only add a check); I2 (the state is run facts, not prose
framing — the ablation is exactly the measurement that shows why).
**Cost:** local backend; their measured ceiling: 1 call, ~US$ 0.00005 per stop that reaches the
question, 17.7% of stops do.

---

### Phase 3 — Decisions you can see, and a gate in front of the bill (surfaces: `apps/desktop/`, `chimera/decisions/factory.py`)

**What will be done:** observability and spend discipline — the unglamorous half of every good
ecosystem project, and the prerequisite for turning anything below from shadow to enforce.

1. **Decisions screen v2.** The existing screen gains: live tail of the decision log with each
   answer's p, band region and receipt; cost and latency per decision folded into the Usage screen;
   a **shadow panel** — what each shadow-mode spec *would* have done — because a quiet shadow log
   proves nothing (hermes's own doc names this: bound by the clock, not the count, and read it in
   the open). No new decision is introduced by this UI; it only makes the ones that exist readable.
2. **Drift alerts without labels.** Tiltmeter's three alarms, all computable from our log alone:
   the version of the serving model changed; a question's answer distribution drifted (PSI, tested
   by chi-square, their method); an answer sits within ε of a decision threshold (a flapping region).
   These are ANNOTATE-level: they surface on the screen and in `chimera decisions report`, they gate
   nothing by themselves — but a refit that follows a drift alarm can then be justified by the log
   instead of a hunch.
3. **The spend/rate gate.** `decision-gate`'s contract, in front of every hosted-backend ask: wait
   while ≥ 80% of a configurable RPM/TPM budget is in flight; honour `Retry-After` on 429; a per-key
   daily USD ceiling (default small, set in Settings next to the System One card); an optional
   response cache keyed the way our `DecisionCache` already keys. **The refusal path is the one
   thing designed here**: ceiling-exceeded and rate-saturated both fail toward scrutiny — a gated
   ask that would have raised REVIEW produces the REVIEW card with `gate: budget` on the receipt;
   nothing is waved through because the meter ran out.
4. **Redaction discipline, stated per surface.** hermes-jev-skills documents, field by field, what
   leaves the machine for each of its eleven skills; we do the same one page for our hosted paths:
   what each decision's state carries, the caps per field, the opaque-id substitution for paths and
   store ids, the never-sent list (secrets, credential-shaped strings, person-marked content), and
   the private-profiles-style opt-out that sends coarse features only. This is documentation plus
   the scrubber wiring it names — no new model call — but it goes in this phase because it must
   precede any surface below that widens what a hosted backend sees.

**Gate:** tests + screenshots; the gate's behaviour is table-tested (saturate, 429, ceiling) against
a fake endpoint, no live API.
**Invariant served:** I8; and the legal boundary from study 22 §Architecture B — a hosted backend is
the user's key, their spend, and their redaction choice.
**Cost:** US$ 0.

---

### Phase 4 — Extraction cascade, coarse-when-unsure (surfaces: `api/attachments.py`, `fusion/`)

**What will be done:** the SDE cascade and the confidence cookbook, as the second consumer of the
architecture our `verified_cascade` already built — the verified-answers path checks *prose answers*;
this one checks *structured fields*.

1. **Per-field Noul battery** over anything the agent extracts from an attached document into a
   structured record (tables, invoice fields, dates, entities): for each non-empty field, the
   cookbook's five gating heads — `hallucinated` (unsupported by the source), `off_target` (pulled
   from incidental text), `incomplete` (wrongly empty for a required field), `format_violation`,
   `unreasonable` — plus the single `absence_wrong` head on empty fields (the cookbook's measured
   asymmetry: absence is the one thing a blank field can be wrong about). One holistic
   "escalate this record?" head is *displayed* but **never gates** — the cookbook is explicit that
   the gate is the per-field battery, and our overseer-battery result (I6) says the same thing from
   the other side: decompose only what separates.
2. **Escalation is per field, then per record:** any head over its threshold re-asks *that field* on
   the strong model; only a record still failing after the pass escalates whole. This mirrors
   verified_cascade's shape (cheap draft → typed verify → strong model → typed verify → admit
   failure) with fields instead of answers.
3. **Coarse-when-unsure for taxonomies** (classification_using_confidence): wherever a Choice picks a
   label from a hierarchy we control (card categories, cron job names, doc types), low confidence
   reports the parent instead of the forced child — no second call, the parent derived in code.
   The rule ships with the cookbook's measured framing attached: forced labelling was 40% right on
   the unsure half; reporting one level up made the same answers 70% useful.
4. Shadow first: the battery runs beside real extractions and records what it would have flagged,
   against the extractions the strong model would have had to fix anyway.

**Bench (pre-registered):** extend `bench/verified_cascade`'s harness to a field-level corpus
(≥200 records from attached-document runs, known truth per field). Primary: wrong fields shipped,
paired against today's no-battery extraction, Holm-corrected; secondary: cost multiple (the
verified_cascade measured 1.87× with the local verifier — the pre-registration states the expected
range and the study is corrected against it). Kill criterion: any field the battery escalates that
was already correct **and** the escalation changes it wrong — that is the cascade breaking a good
answer, the one direction I1 forbids.
**Invariant served:** I6 read the cookbook's way (atomic, discriminating atoms only — `destroys`-style
shared-property atoms stay out); I1 (escalation only).
**Cost:** ~US$ 2–5 local, mirroring verified_cascade's US$ 4.89.

---

### Phase 5 — Fused rerank, literal compaction, web screening (surfaces: `rag/hybrid.py`, `core/context_budget.py`, `tools/web.py`)

The three riskiest mechanisms, in one phase because they share one property the bench design must
hold: **each replaces part of a pipeline a deterministic stage already owns**, so each is measured
fused, not standalone.

1. **Rerank fused into RRF.** jevsearch's recipe on our hybrid retrieval: the RRF order stands as
   the shortlist (top-k, k=20 — their accuracy plateau), one `noul` per candidate ("would this
   passage answer the query?") plus one "does anything answer it at all?" head, the final order
   `0.75 × relevance + 0.25 × best-answer-share`, below-threshold hits **fall back to RRF order**,
   not dropped into silence. This is I7 made concrete: the decision reorders what the deterministic
   stage produced and inherits its floor on failure. The pre-registration carries the study-22
   number that forbids the standalone version (rag_rerank alone: −7.8 pp recall@10) so nobody
   re-runs the refuted arm out of curiosity. Also pre-registered as a comparison arm: a local
   Qwen3-Reranker, the alternative the ecosystem's null result names.
2. **Literal keep/drop compaction.** The jev-compaction contract on our ContextBudget: a `noul` per
   tool call / result "still needed?", kept segments stay **verbatim**, dropped ones move to a store
   behind an `expand()` pointer rather than dying, the prefix that the cache keys on is frozen
   append-only, and the active-restoration re-injection (working file, plan, task list) is unchanged.
   The hermes measurement is written into the pre-registration as the constraint the design already
   satisfies: a handoff *written* by Jev recalled less than the literal transcript — so nothing this
   phase ships is generated text; it is selection. `summarise_compaction` stays off; this is its
   measured alternative, not its companion. Bench: the study-25 handover recall protocol, 3/3
   recall held, plus token savings, on the same paired transcripts useful_context used.
3. **Injection screening on tool results.** hermes web-screen's mechanism for our `web_search` /
   scrape results: a `noul` per chunk ("does this carry instructions aimed at the agent?"), the hit
   withheld **before the model reads it**, replaced by a marker; a local pattern screen still runs
   when the backend is down (their 0/1,520 clean chunks withheld is the false-positive budget we
   pre-register against). Withholding content from the model is scrutiny-adding, so this is the one
   place a screen can act without a shadow phase delay — but it ships shadow anyway, because the
   planted-attack corpus has to be built and measured first (their eval: 70/79 planted, in-distribution,
   recall not transferable, and they say so).

**Bench (pre-registered, one per mechanism, never pooled):** rerank — recall@10 / MRR on our own
labelled retrieval set vs RRF alone vs Qwen3-Reranker; compaction — recall protocol + tokens, paired;
screening — planted-attack corpus (≥80 planted, ≥1,500 clean), recall and false-withhold, local vs
hosted arms. Each carries its own kill criterion: rerank fails if it loses to RRF on recall@10;
compaction fails if any recall check drops; screening fails if clean-withhold exceeds 0.5%.
**Invariant served:** I7 in all three; I2 for the screening state (chunk alone, no page framing).
**Cost:** ~US$ 10–15 across the three, local backend where the models allow.

---

### Phase 6 — What stays closed, written down (US$ 0)

Not a phase of work — a phase of **record**, so the next study does not have to re-litigate:

- **Full decision-loop agents** (jevloop, jev-ultrafast, jev-for-chrome, jevonian's full-prompt
  brain, jev-social's operation loop): the ecosystem's most popular pattern, closed here. B4 and
  B4b measured our version of it; the FastAPI case study's 3.2× cost saving is real *for its lane*
  (conventional file editing, its own caveats say so) and does not transfer to a loop whose
  executor is a frontier model that already routes well.
- **Auto-approve hooks** (jev-auto-approve, jev-guard's deny path, dsh-jev-interceptor's block
  path): the study-22 §5 "never" row stands; the ecosystem's own best numbers (their published
  calibrations) are rejection rates on *their* corpora, not a transferable operating point.
- **Generated-text compaction** (hermes handoff with HANDOFF_JEV=1): measured worse by its own
  authors; stays off.
- **Trading and finance** (jev-trade, QuantDinger, jev-trader): out of product scope; the one thing
  adopted from QuantDinger is its **strict response validation** — choice must be the argmax of a
  complete, summing, in-range distribution or the answer is an error, never a coerced result — which
  phase 0's compat suite encodes.
- **`augustus-train` / LoRA heads** (Architecture C in study 22): unchanged gate — ≥300 labelled
  items per family, beat calibrated logprob on a held-out split; the MCA §2.3(b) boundary from
  study 22 is restated as binding: nothing of ours is tuned against Jev answers, ever.

## 3. The phase table, with its gates

| Phase | Surface | Ships | Pre-registered bench | Gate to enforce | Cost est. |
|---|---|---|---|---|---|
| 0 | cli, decisions/lint, api | exit-code contract; 3 linter rules; compat suite | tests + sabotage | — | US$ 0 |
| 1 | scheduler | wake gate (shadow) — **ran: NULL** | ≥30 labelled cron scenarios, 0 false skips | bench + a week of shadow log read | ~US$ 0 |
| 2 | core agent | stop gate (shadow) — **ran: NULL** | 3-arm ablation, AUROC CI, threshold sweep | beat wording ≥ +0.08, wrong-blocks ≤ 2% | ~US$ 1 |
| 3 | desktop, factory | Decisions v2, drift alerts, spend gate, redaction doc | table tests, no live API | — (infra) | US$ 0 |
| 4 | attachments, fusion | field battery + coarse-when-unsure | ≥200 records, paired wrong-fields-shipped | significant fix, no broken-answer regressions | ~US$ 2–5 |
| 5 | rag, context, web | fused rerank; literal compaction; injection screen | 3 separate pre-registrations | per-mechanism kill criteria above | ~US$ 10–15 |
| 6 | docs | closed verdicts recorded | — | — | US$ 0 |

**Pre-registrations written with this plan** (each in its own directory, the house format —
question, instrument byte-for-byte, arms, items, metrics, decision rule fixed before any call,
prediction, §2q):

* `bench/wake_gate/` — phase 1. `PREREGISTRATION.md` (the registered policy table, the three-way
  Choice, the ≥30-scenario corpus rules), `run.py` (the runner: lint → corpus check → one pass
  local → registered metrics + floor sweep → the rule read out of the numbers), `README.md` (the
  corpus checklist — `items.jsonl` deliberately does not exist yet; labelling precedes the first
  call).
* `bench/stop_gate/` — phase 2. `PREREGISTRATION.md` (the two belts, the four questions — `outcome`
  is a Score, its veto in code — the three-arm ablation on one pass of answers, the
  ΔAUROC-with-CI gate), `RUBRIC.md` (what a false done is; the two hard cases decided now), `run.py`
  (`label` / `ask` / `ablate`; the ablation is free after one pass), `README.md` (the order that
  makes this a pre-registration, and the one step the owner does: wiring the transcript paths).

Both run local-only (Ollama `qwen3:4b`), US$ 0, and both ship shadow at most — nothing sleeps and
nothing is nudged by these benches; each enforce question gets its own registration.

## 3b. Review after phases 1–2 ran (2026-09-29)

Both benches that ran came back **NULL**, and both nulls say more about the apparatus than about
the mechanism. The premises they rested on did not hold on this project:

- **Phase 1 assumed a daemon that wakes on events. Ours does not.**
  - `chimera/scheduler/` dispatches on timers. The only webhooks it knows are *outbound*
    (`delivery.py` posts answers).
  - 17 of the 36 scenarios were inbound webhook events the daemon cannot receive.
  - All 36 were about one job, which runs once a day on the desktop.
  - The mechanism that could exist here is a narrower "skip this tick?" question, and the money it
    could save is on the VPS, where ~15 LLM jobs run daily, not on a desktop with one.
  - Any successor registration starts from the VPS `cron_results.jsonl`, over timer ticks only.
- **Phase 2's labels are the wording it was meant to see past.**
  - 118 of 120 edited turns ran no check, so `false_done` reduced to "claims done", which is arm
    A1's own question.
  - AUROC 0.98 in every arm is agreement between two readers of the final message.
  - Ground truth has to come from what happened after each turn; see `bench/stop_gate/RESULTS.md`.
- **Both corpora were built and labelled by the same model family that wrote the questions.** The
  pre-registrations declared this. After seeing the results, it is the main risk to carry into any
  later phase: every later bench needs labels that come from outcomes, not from reading.

**Recommended order for what remains:**

1. **Phase 0** (US$ 0: exit codes, 3 lint rules, compat suite). It needs no corpus, and both runs
   above tripped on lint problems it would have caught.
2. **Phase 3**, the observability half (Decisions v2, drift alerts, spend/rate gate, redaction page).
   It is infrastructure the later phases need, and it has no measurement premise to fail.
3. **Phase 4, only with an outcome-labelled corpus.** The field-extraction battery can reuse
   `bench/verified_cascade`'s machinery, where truth per field is known by construction. That is
   the one phase whose ground truth does not come from a reader.
4. **Phase 5 last, one mechanism at a time.** The fused rerank has the strongest prior (the
   ecosystem's jevsearch, our own −7.8 pp note on the unfused arm). Compaction and injection
   screening need the corpora the plan names, and neither exists yet.
5. **Phases 1 and 2 are reopened only as new registrations,** on the corpora named above (VPS ticks;
   turns with outcomes).

## 4. What this study cannot show (§2q)

- Every number quoted from the ecosystem is its authors', on their corpora, at their pinned model
  versions (`jev-1.13.0` at their measurement dates; aliases move). None has been reproduced here;
  each phase's bench exists precisely because ours is the only number that counts for our thresholds.
- Several sources are single-author, same-day releases (the awesome-jev curation warning applies to
  wakegate, jev-use and jev-superpowers alike); where a mechanism was adopted anyway, it was adopted
  for its *measured* property, not its stars — and the two adopted without a usable measurement
  (the spend gate, the redaction page) are adopted as *designs*, not results.
- The wake gate and stop gate change behaviour in the direction of *fewer* turns and *more*
  verification respectively; neither direction has been measured on Chimera at all yet, and nothing
  above should be read as having been.
- The X threads and several linked pages in the ecosystem list were not fetched this run (the list
  itself was); where a claim rests only on a thread, it is not cited above.
