# Study 18 — the arXiv sweep read against the three surfaces

Run 2026-09-12/13 at Bruno's request: eleven agents over the ~100 category listings he named, filtered
by one question — *can this change the terminal agent, the desktop app, or the site?* Roughly 5,000
titles surfaced, ~450 abstracts opened, ~90 items proposed, and everything below survived a second
pass in which the claim was checked against **our own repository** before it was allowed onto a list.

Reading order matters here. The value of this sweep is **not** the item list: it is three findings that
several disjoint groups reached independently, and six places where the literature corrects something
we had written down. The item list is last, and it is short on purpose.

---

## 1 · Three theses the sweep converged on

### 1.1 Detection does not work. Isolating capability and restricting destination do.

Reached by two agents from disjoint literatures (cs.CR and cs.SE), with numbers on both sides:

| evidence | number |
|---|---|
| `2608.27092` — explicit injection refused by gpt-4o | **0%** executes |
| …the *same* exfiltration reframed as an "integrity signature" | **100%** executes |
| …destination allow-list | **0%** |
| …planner/reader split | **0%** |
| …SecAlign / channel separation (recognition-based) | 32.5% / 38.8% |
| `2609.08371` (CapScope) — injected effect under ambient authority | **33–47 / 75** |
| …under typed capability held **outside** the model's context | **3 / 75**, legitimate repairs 68/75 vs 68–72/75 |
| `2606.15549` — 1,709 real denylists (13,332 commands) bypassable | **69.0–98.6%** (Claude Code's included) |
| `2608.27009` — six published semantic guardrails, over-refusal on *authorised* action | **35% – 89%** |

The two halves fail in opposite directions, which is the whole point: a lexical denylist is bypassed by
rephrasing, and a semantic guard refuses legitimate work at a rate none of us would ship. **Our kernel is
the lexical half plus verbatim-span taint — the class measured as failing, because a paraphrase launders
the span.** The combination with numbers behind it is taint + capability narrowing + **allow-list on
destination**, and we have that combination only half-built.

Worth stating plainly: our own measured over-refusal of **15–25%** on the ambiguous corpus (#452) is
*better than all six* published guardrails. The library decision holds, and now holds against a field.

### 1.2 The per-action human gate is a record that someone clicked, not a control.

Two groups, real humans, different designs, same conclusion:

- `2604.04918` (N=48, within-subjects, live web, an embedded problematic action per task): exposure under
  **action-confirmation 88.5%** vs **plan-gating 60.4%** (p<.001). Ability to *correct* once visible does
  not differ between regimes (9.2%–26.4%, p=.407). The gain is in exposure, never in correction.
- `2606.05647` (**n=107 real developers**, ~5 h task, real sabotage, 4 frontier models): **94% do not
  detect**. With a monitor, 63%. And the number that decides the design: of the 16 sessions where the
  monitor **alerted correctly, 9 (56%) approved the malicious code anyway** — 67% of those after minimal
  review. Declared preference: **70% want recommend-and-wait, 9% want flag-only** — and flag-only is what
  we ship.
- `2608.27443` (**n=113, pre-registered on OSF**): letting the user author permission rules protects
  **less** than per-action approval — overreach blocked 39.6% vs 59.6%, **−20.1 pp [−32.1, −8.1]** —
  because **81.4% of authored rules become "ask"**, handing everything back to the runtime. The promised
  time saving does not exist. *(After Holm correction only the HITL contrast survives; do not cite the
  −14.5 pp against AUTO.)*

### 1.3 A cheap deterministic check beats a smarter LLM judge, repeatedly and by a lot.

This one arrived from five directions and is the most useful thing in the sweep:

| task | best LLM judge | deterministic alternative |
|---|---|---|
| `2606.09863` false-success detection (9,876 + 1,879 trajectories) | **no config above AUROC 0.65** (5 judges × 5 prompts) | TF-IDF **0.83 / 0.95**, 4–8× more catches at equal flag rate, **3,300× lower latency** |
| `2609.07162` single-trace monitoring | 9 LLM monitors, 100% → **60.9%** as TV → 1 | a **20-line relevance check: 100%**; and *executing* the second pass 90.0% vs *imagining* it 50.4% |
| `2608.02464` agent-failure detection | learned monitor 54% at 17% FP, and **does not transfer** (AUROC 0.527 cold) | recompute + coverage: **60% (96%) of failures at 0 / 63 false positives**, fires on **0 of 1,825** healthy episodes |
| `2609.11646` "was retrieval enough?" | local LLM judge **0.649** | distribution-shape features **0.856 at 2 ms/query** (~3000× cheaper) |
| `2607.25152` accepting a self-improvement cycle | strongest in-band judge accepts cycles of which **44% are regressions**, rejects 38% of real gains — the pre-registered "a stronger judge closes the gap" hypothesis was **rejected** | where the success spec is verifiable in the artifact, the mirage goes to **zero** |

The cure is never a better judge. It is **out-of-band** evidence and arithmetic.

---

## 2 · Six places the literature corrects our own record

These are worth more than any item, because each one is a sentence we had written that is now wrong or
overstated.

1. **"The noise floor became consensus in six independent papers"** (`chimera-study-16`) counts
   *authorship* independence as *evidential* independence. Checked: the six author sets are genuinely
   disjoint — but **three are solo-author, unaffiliated, 6–13 page pilots "under review"**. The
   convergence is real; the weight is not six papers' worth.
2. **Our Wilson intervals over k runs of the same item are inflated.** `2609.06386` (n=24,998 groups)
   measures intra-group verifier-error correlation at **0.530**, i.e. **effective n = 1.70 for a group of
   8** — a design effect of ~4.7×. Anywhere we compute a CI over k outputs that passed the **same** gate
   (pass^k, replicated arms), the margin is narrower than the arithmetic says.
   ⚠️ **TESTED 2026-09-13 and half wrong** (`bench/design_effect/RESULTS.md`): our measured ICC is **0.706**,
   higher than theirs — but `replicated.py` already pairs on **per-task** `pass^k`, so the margin it publishes
   is not inflated. Reading the paper said the mechanism exists; only reading our code could say we had it,
   and this item skipped that step. The number prices replicas instead: k=3 buys 1.24 observations.
3. **We measure the wrong noise axis.** `2608.22331`: semantically-neutral **prompt perturbation** SDs are
   **11× to 58×** the rerun SDs. Our three measured floors (seed 4.7 pp · sampling 2.3 pp · ruler 0) have
   no perturbation axis — *and `bench/PROTOCOL.md` §5 already requires one*. The rule is written and, as far
   as the tree shows, never executed. That is the §2t shape on ourselves.
4. **Our fusion panel diversifies the wrong thing.** `2609.10969` (2,880 scenarios): cross-model voting over
   **shared** evidence approves **62.9%** of unsafe proposals vs **22.9%** with independent sources — a
   **40.9 pp** source effect against an **11.3 pp** model effect. **Evidence diversity is worth ~3.6×
   model diversity**, and our panel varies model while sharing the evidence.
5. **The "93% of users approve permission prompts" figure has no study behind it.** Two agents found this
   independently: it traces to a vendor engineering blog quoted inside a paper that ran no study. The real
   measured number is 66.8% overreach approval in the POLICY arm of `2608.27443`.
6. **Three strong candidates died on a `grep` of our own tree**, and one gap we thought open is closed:
   `2609.05767` (memory/allergy) is **already mined** in `bench/memory_prune/PREREGISTRATION.md`;
   `2609.08589` (progress bar) recommends a rule **already in `chimera/core/ledger.py:12`**;
   `2608.01347` (prompt-induced waste) finds boilerplate our prompts do not contain; and `2609.07754`
   (assistants install without checking, 0.5% of 1,920 trials) is **already closed at
   `chimera/governance/policy.py:154`**.

---

## 3 · The shortlist — what is cheap, new, and verified against our code

Ranked by (evidence × implementability × not-already-done). Everything here was checked against the tree.

| # | item | the change | evidence | surface | effort |
|---|---|---|---|---|---|
| 1 | ~~**`2606.09863`** false-success detector~~ **MEASURED — DO NOT BUILD** | a cheap lexical detector over each run's final completion claim, flagged in the Runs tab | claimed AUROC 0.83–0.95. **Measured on our own 547 solves: 0.9342 random-split, 0.5996 leave-one-task-out** — the published band is a task-identity leak, and a character count matches the model (0.6023). The phenomenon is real and worse than reported (36.1% of our claimed successes are false); the *detector* is not. `bench/false_success/RESULTS.md` | terminal + desktop | **done, US$0** |
| 2 | **`2604.04918` + `2606.05647`** gate the plan, recommend-and-wait | move the approval gate from per-action to **per-plan** (we already have the plan step), and make the prompt state a recommendation with a spec-anchored reason, **defaulting to the safe option** | exposure 88.5% → 60.4% (p<.001); 56% approve after a *correct* alert; preference 70% vs 9% | desktop | **S** (gate) + **M** (prompt) |
| 3 | **`2606.15549`** denylist bypass battery | run their bypass classes against `chimera/governance/policy.py` and publish the result | 1,709 real denylists **69–98.6% bypassable** | terminal | **S** — *the cheapest test that can refute us* |
| 4 | **`2609.02983`** gitleaks defect | pin the gitleaks version in `ci.yml` and add a boundary-mutation battery per rule | **confirmed source defect in 8.21.2**: detection 0.9976 → **0.5233** for a credential ending in a hyphen; we have had a plaintext token before | terminal/CI | **S** |
| 5 | ~~**`2609.06386`** design effect~~ **MEASURED — the prediction below was HALF WRONG** | apply the intra-group correction before declaring any margin over k same-gate runs | **Our own ICC(1) is 0.706** (median of 8 factorial arms, 0.53–0.77) — *higher* than the paper's 0.530. But the exposure §2 claimed is **not in our code**: `replicated.py` pairs on per-task `pass^k`, so no interval it publishes is inflated. The number prices REPLICAS instead: k=3 buys 1.24 observations, the 3rd run adds 0.07. One genuinely un-clustered site named, not corrected (`auto_evolve` panel). `bench/design_effect/RESULTS.md` | benches | **done, US$0** |
| 6 | **`2608.22331`** perturbation floor | execute the paraphrase arm `PROTOCOL.md` §5 already requires | perturbation SD **11–58×** rerun SD | benches | **S/M** |
| 7 | **`2609.10969`** evidence lineage | record the evidence each panel member saw; treat shared evidence as a *correlated* vote | source effect 40.9 pp vs model effect 11.3 pp | terminal (fusion) | **M** |
| 8 | **`2609.07360`** harness scanner | run their instrument over our own `skills/` and MCP declarations | 16.0% of 3,171 repos carry a validated defect; `Bash(python:*)`-shaped grants are the pattern | terminal + desktop | **S** |
| 9 | ~~**`2609.06993`** asymmetric delimiters~~ **AUDITED — one real defect, fixed** | machine-consumed payloads stop using symmetric fences; boundary check separate from content check | **Six fence strippers audited; five anchor to the whole string and are correct.** `core/spec_test.py` used `re.MULTILINE`, where `$` matches at every line end — `re.sub` deleted EVERY fence in the reply. Measured: a valid module whose docstring quoted an example lost both inner fences and five lines, silently. Boundary now decided once, content returned byte-for-byte, an unclosed opener reported not repaired | terminal + site | **done, US$0** |
| 10 | **`2609.06815`** typed skill cards | render skill cards as typed fields instead of one markdown blob | typed vs single string **+8.5 pts** clean, +7.4 under 60% injected contradiction | terminal | **S/M** |
| 11 | **`2609.05339`** memory survives an upgrade | store `embedding_model_id` + schema version per fact, keep the raw evidence beside the summary, full re-embed on an embedder change | repair from the store alone **0/48**; with retained source **34/48**; fixed-schema facts are upgrade-invariant | terminal | **S/M** |
| 12 | **`2609.09372`** IRT over our own logs | fit a 2PL over the item×arm matrices we have already logged | choosing by aggregate rank displaces ~22% of appropriate picks; per-item difficulty separates informative holdout items from noise | benches | **M** |
| 13 | ~~**`2605.29442`** summary vs reality~~ **MEASURED — DO NOT BUILD** | in verify, diff the agent's written summary against the real diff and test output | **Measured on the same 547 solves as #457**: claim-vs-diff **0.6643** within task (the ONLY leak-free signal either bench found — random split 0.6621, gap −0.002), against 0.5996 for the claim alone. Below the registered bar. The richer arm (claim vs everything TOUCHED) was **worse**, 0.5189: widening destroys the contrast. Rule 'claims a check, ran nothing' fires 8×, 5 right. `bench/claim_vs_diff/RESULTS.md` | terminal + desktop | **done, US$0** |

---

## 4 · Do **not** build these — each is a measured failure

The strongest section of the sweep, because each of these is something we would plausibly have built.

- **Confidence shading per token or per step.** `2605.28571` (n=192, baseline without uncertainty):
  fine-grained uncertainty *raises* agreement with the AI (0.674 → 0.730) and *halves* independent
  verification (link-checking 0.557 → **0.318**), with **accuracy unchanged**.
- **A "write your own permission rules" UI sold as a control.** §1.2 above: −20.1 pp against per-action.
- **An "AI can be wrong" banner on the site.** `2606.21317` (**n=2,610**, pre-registered): moves perception
  (d ≈ −0.11 to −0.18), **does not move behaviour** (all p > 0.11), and costs −0.15 d of return intention.
- **A numeric reliability label on tool returns.** In `2609.05587`'s own body this *increased* adoption of
  the corrupted value.
- **A stronger LLM judge to close a verification gap.** `2607.25152` pre-registered that hypothesis and
  **rejected it**; `2609.07162` gives the ceiling (≤ ½ + ½·TV) that explains why.
- **An importance scorer for memory eviction.** `2609.08279`: policies with paired accuracy do not differ;
  **budget size dominates policy**.
- **Mutation testing for the diff gate.** `2609.09315` (6,000+ defective instances): beats coverage only
  marginally and does not justify the cost.
- **Promoting `PlanPreview` to the primary review surface.** `2609.09038` (n=50): the format people
  *prefer* is the **worst** for finding errors — error localisation CoT **95.5%** vs Plan-and-Solve
  **57.1%**, and Plan-and-Solve is the most-preferred.

And one guard to attach to any desktop work: `2602.16844` (Magentic-UI, n=12×3) made review **faster and
more confident and not more correct** (accuracy g=0.18, null; **confidence-when-wrong g=0.85**). Any change
to the Runs tab pre-registers **accuracy and confidence-when-wrong as a pair**; "easier to review" without
accuracy moving is to be read as harm.

---

## 5 · The hole, and the one place publishing beats reading

**Nobody has studied humans supervising several concurrent agents.** OrchVis: no study. AgentGUI: asserted
in the abstract, **not measured**, sent to future work by its own authors. The literature on supervising N
parallel systems is robotics, from 2023. Across the four interface papers verified in depth: **56 humans in
controlled conditions, of whom 48 supervised a *simulated* agent and 8 read a real trace.**

Our Orchestration tab is not under-researched only here — it is under-researched in the field. There is
nothing to copy, and a small honest study would be the first. That is the only item in this sweep where
producing evidence is worth more than consuming it.

Second transversal null: of the interface papers, **only one measures how much error the supervisor let
through** (`2606.05647`: 94% / 56%). The rest measure speed, preference or task success.

---

## 6 · What to never cite, and the shapes that produced it

Roughly forty papers were rejected by name across the eleven reports. The recurring shapes, so the next
sweep can reject faster:

- **Participants who are not people.** `2606.08919` ("oversight has a capacity") builds its κ=0.52 from
  **three LLM personas** and its inverted-U from a fatigue curve whose slope the author chose — the body
  says so, the abstract does not. `2604.00892`'s "user who changes their mind" is Claude-Opus-4.5.
  `2606.17099`'s "192 reviews" are LLM judges. Standing counter-citation: `2601.17087` — agent success
  moves up to **9 pp** by swapping the LLM that plays the user, with systematic miscalibration.
- **A number the authors' own model produced, printed where a result goes.** `2606.22484` (84–97% velocity
  "preserved"), `2609.10615` (RMSE 0.571→0.159 on the authors' own synthetic data).
- **A defence that reports its block rate and never its false-refusal cost.** `2609.06500` (99.5%
  privilege reduction, **zero attacks evaluated**), `2605.20734`, `2609.11024` (55%→0% violations, never
  measures whether the task still completes), `2609.10854` (98.9% recall = 143 flags for 94 true, ~66%
  precision, never called a cost).
- **Ranges that carry no information.** "0.24–74.20%", "1.25×–13.28× (from simulation)", "up to" on n=4.
- **A release that does not exist.** Verified by API against a deliberately-invented repo id:
  `2609.03181` (Jina-OCR-v1) and `2609.09895` (VidHalLoc) both advertise public artifacts that are not
  there — the second being a paper about whether hallucination detectors can be trusted.
- **An abstract that cannot state its own size.** `2609.11030` shipped with unrendered LaTeX macros where
  every headline count belongs.
- **The paper that flatters us.** `2603.10664` ("Terminal Is All You Need") argues our own design from
  "established HCI theory" with **zero measurements** — the one paper in the batch most likely to be cited
  by us and least entitled to be.
- **An entire subfield with no published null.** ~12 sampled "self-evolving skills" papers: zero negatives,
  ablations in which every component contributes, gains of +25.83 points. Until one of them publishes a
  negative, the cluster is not a source — which is consistent with our own evolution results.

---

## 7 · Coverage, honestly

- **Scanned:** ~5,000 titles across the ~100 listings. Complete listings: cs.MA (89), cs.CV (776/776),
  cs.IR (118), cs.DB (42), cs.PF (28), cs.NI (91), cs.AR (77), cs.ET (42), math.OC (255), math.PR (261),
  math.NA (195), math.LO (49), and the 2,479 unique math titles. Partial: cs.AI (entries 1–420), cs.CL
  (611 of ~655), cs.DC (126/137), cs.SE (~184 of 279 in-window).
- **Zero-yield categories, well supported:** cs.CC, cs.DM, cs.CG, cs.SC, cs.GR (46), cs.MM (36), cs.DL (21),
  cs.AR, cs.ET, eess.SP (167), eess.IV (52), and 674 math titles across 17 subcategories. **Recommend
  dropping these from the next sweep.**
- **Instrument failures, recorded rather than hidden:** the arXiv API returned HTTP 429 to several agents
  (one lost all 20 of its paginated queries), and `show=2000` pages truncate. Every per-category "0
  relevant" therefore means *0 among what was surfaced*, a floor rather than a census — our own §2ac.
- **The listing was the wrong instrument for four themes.** GUI agents, document parsing, tables and
  caching produced nothing from the recent listings and everything from targeted search. Next sweep should
  be theme-first, category-second.

---

## 8 · Suggested order, if any of this is picked up

Nothing here is started. The natural order by cost is: **#3 and #4** (they can refute us, and cost
almost nothing) → **#1 and #5** (cheap, and both correct a number we currently report) → **#2** (the
desktop change with the best human evidence in the sweep) → the rest. The "do not build" list in §4 is
free: it is work avoided.

---

## 9 · Addendum — the developer over-reliance lane (arrived after §1–§8 were written)

A late lane surveyed **~180 titles on over-reliance in coding assistance, opened 35 papers, kept 17 and
rejected 33 by name**. It does not change the three theses; it hardens §1.2 and §4, and adds four threads
that cut across papers with **real human participants**.

**Thread 1 — review time is not scrutiny.** Three independent apparatuses say it: an LLM label raises
fixation **+33% to +60%** while saccade length stays flat (γ=0.01, CI [−0.07, 0.10], a clean null);
explanation moves confidence and agreement while review time does not move at all (**p=0.57**); and patch
*priority* moves time and cognitive load without moving the merge decision. **A harness that logs "review
time" as evidence of care is measuring the wrong thing.**

**Thread 2 — every intervention in the batch either did nothing, cost something, or inverted.** The
roll-call, which is a stronger version of §4: security hints **0 pp** (the Gemini arm did not move a
digit); a *correct* monitor alert converted to action only **44%** of the time; an under-specified
explanation **lowered accuracy (OR 0.58) and raised confidence (3.99 → 4.25, p=0.005)**; a "what if"
cognitive forcing function pushed over-reliance **70.6% → 80.7%**, and stacking it with the one that
helped gave **exactly 0**; delegation contracts bought reviewability for **+13% tokens and +38%
wall-clock** with **zero** correctness gain (64/64 passed either way); copy-friction more than doubled
rework. **Nothing in this literature supports "add a warning and the human will verify."**

**Thread 3 — self-report errs large and directionally, which invalidates a class of gate.** METR's RCT:
predicted −24%, self-estimated −20% *after living it*, **measured +19%** — a 39 pp error in the wrong
direction. Programmers judge correct assertions at **73.9%** and incorrect ones at **49.0%** (coin flip)
**with statistically identical confidence**. 84% report a productivity gain while the share reporting a
worse experience nearly doubles. Participants do not notice a label influencing them. **Any Chimera gate
whose input is "the agent says it verified" or "the developer says they reviewed" is invalid by
construction** — which is the independent case for shortlist item #1 (a deterministic detector over the
completion claim) and #13 (diff the written summary against the real diff and test output).

**Thread 4 — only one paper in the lane measured the damage after switching the intervention on.** The
rest estimate the gain beforehand. That is our own §2r, now visible in someone else's literature.

**Two additions to the shortlist context:**
- **`2607.08885`** is the most on-point paper for our spec-test work: it measures humans judging
  *assertions*, which is exactly what `core/spec_test.py` emits, and finds judgement at chance on the
  wrong ones with unchanged confidence. Any future "show the generated test to the user" surface should
  pre-register against it.
- **`2509.08514`** (n=2,784, randomised factorial) supplies the hidden cost of any "require the human to
  fix it" design: **under-correction rises as correcting gets effortful**, and two intervention arms
  (seeding 100% errors early; paying for performance) were **null**.

**One more standing rule, and the pair that backs it.** `2601.17087` (agent success moves up to 9 pp by
swapping the LLM playing the user; miscalibration is systematic and directional) together with
`2605.20767` (an RCT with LLM-simulated users **is** an observational study, because the intervention
shifts the persona's latent attributes, so the arms hold different implicit populations; it proposes
negative-control outcomes as the diagnostic) is the citation pair for discarding any simulated-user
evaluation — ours included, if we ever build one.

**Also flagged, and consistent with §6:** `2606.17099`'s "three independent reviewers blind to condition"
are **models, not people**; and the Microsoft "+24% PRs merged" telemetry carries **no measure of
verification or trust**, while its proxy (merged PR) is precisely the quantity METR showed diverging from
measured time.
