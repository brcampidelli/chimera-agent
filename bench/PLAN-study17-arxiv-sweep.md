# Working plan from study 17 — the arXiv sweep of 2026-09-11, ranked by what it changes

Written 2026-09-11 after screening **4,995 abstracts** (ten `self-improve` search pages plus the
"recent" listings of nineteen categories: cs.AI, cs.LG, cs.CL, cs.IR, cs.HC, cs.MA, cs.SE-adjacent,
cs.PL, cs.OS, cs.PF, cs.DS, cs.CC, cs.CE, cs.CG, cs.AR, cs.LO, cs.MS, cs.MM, cs.IT, eess.SY) in
sixteen slices, one triage agent per slice, against a shared rubric (`RUBRIC.md`: the product state
as of 0.53.0, fourteen open problems, and the report format). Memory:
`chimera-study-17-arxiv-sweep`.

Two disciplines from the earlier studies were kept, and both paid again:

- **Grep before believing the paper.** Every hook below that says *verified* was opened in the repo
  today, by file and line. One slice report placed `prune` in `chimera/memory/value.py`; it lives in
  `chimera/memory/manager.py:153` (`value.py` only holds the weights). The correction is in the item.
- **A null with numbers outranks a positive without them.** Five of the items that change a decision
  are negative results: they say a thing this project was about to build, or has already built, does
  not do what it is assumed to do at equal cost.

Roughly 4,600 of the 4,995 abstracts were dropped on title and category (control theory, wireless,
accelerators, XR, numerics, image/video generation). About 380 were read in full. Sixty-one were
carried as candidates; **nineteen** survive as things that change a decision. The rest are listed
at the end as nulls, as already-exists, or as skipped clusters, so the same paper is not re-read in
study 18.

The ranking key, in order: (1) does it change a decision already taken or about to be taken;
(2) is the hook verified in code today; (3) how cheap is the measurement that has to come before
adoption. Cost is in model calls, because everything else is hours.

---

## A. Terminal (`chimera chat/assist/tui/solve/approve`)

### A1. The approval question names a tool and nothing else — and the fix is authority, not prose

**Papers.** 2609.08472 (*Cross-Substrate Authority*, slice 16): identical final files can require
opposite safe actions, because authorization state lives outside the workspace the planner sees.
128-cell ablation: authority-blind evidence **0/32** final success vs raw receipts **32/32**; typed
packaging adds nothing over raw receipts; richer evidence shown to the planner still yields
**12/16 unsafe publish decisions**, while a deterministic execution guard replaying the same intents
blocks **6/6** unsafe and permits **12/12** valid with zero model calls. 2609.04679 (slice 15,
n=6 trace study): repair messages that do not translate internal terms into domain-legible revisions;
the four properties a consequential change needs — attributable, inspectable, scoped, contestable.
2609.07162 (*Oversight Gap*, slice 01): information × procedure — a person shown a name cannot
oversee; a person shown the command and its provenance can.

**Verified here.** `chimera/governance/pending.py:82` — `PendingApproval` carries `id, action,
reason, asked_at` and nothing about lineage. `chimera/governance/precedent.py:60-73` — `recall`
reuses a verdict by Jaccard overlap on the action **string**, so two identical strings with different
provenance get the same precedent. Measured 2026-09-11 in `bench/right_hand_governance`: across 7
attacks and 5 legitimate rows the person is shown **6 distinct question strings**, and on the
taint-narrowing path the action is **empty in 12/12** (the question is the tool name).

**Change.** Put on the question — and on the precedent key — the three facts that distinguish two
identical diffs: the command as it will run, the tainted source that narrowed it (`record_fetch`
origin), and the matched span. Key `precedent.recall` on `(action, source lineage)`, not the string.
This is task chip `task_917ac4c5` ("put the command, the tainted source and the matched span on the
approval question"); the paper adds the reason to do it as authority state rather than as a longer
sentence: **showing the planner more did not fix planning (15/32); enforcing at the boundary did (6/6).**

**Measure before adopting.** Re-run the 12 `right_hand_governance` rows and count distinct question
strings (acceptance: 12/12) and, per attack, whether the question names the attacker-controlled
source. Zero model calls — the questions are produced deterministically at the seam.

**Cost.** ~1 day; US$0 to measure. **Cannot show:** whether a *person* answers better — that is A6.

### A2. Package installation is ungoverned

**Paper.** 2609.07754 (slice 14): agents install packages the task text names; the install is the
attack surface, not the command that runs afterwards.

**Verified here.** `chimera/governance/policy.py` — the rule set watches `curl|wget … | sh` and
netcat fed from a pipe; there is **no rule for `pip install`, `npm i`, `uv add`, `apt-get install`,
`cargo add`**. A page that says "install `requests-oauth2`" gets its package installed under
`allow` on the default posture, with taint narrowing not touching it because the tool is `run_shell`.

**Change.** One `Rule("package_install", …, Decision.REVIEW, …)` for installs whose source is not a
lockfile already in the repo; the taint path escalates it to a question when the run consumed
untrusted content.

**Measure before adopting.** `bench/injection`: one attack row where the page names a package;
sabotage-verify the rule (comment it out, the row must go red). US$0.5.

### A3. `solve` verifies with tests that were never shown to discriminate

**Papers.** ExecCritic 2609.09133 (slice 13): a generated test is evidence only if it **fails on the
base and passes on the gold** — tests that pass on both are the majority of what generators emit.
2609.04058 (slice 15, deployed silicon): a full fixed-vector regression **passed while a defect was
live**; the cure was a byte-exact oracle plus a randomized adversarial soak. 2607.25152 (slice 13):
an in-band judge that reads the worker's own account accepts 44%/38% of wrong work.

**Verified here.** `chimera/core/spec_test.py:104` — `SpecTestGenerator.generate` returns the code
if `"def test"` is in it; there is no vacuity check (does it pass on an empty workspace?) and no
base/gold discrimination gate. `CommandVerifier` (`chimera/core/verify.py`) runs what it is given.

**Change.** Reject a generated test that passes on the *pre-patch* workspace (it cannot have measured
the patch); where a gold exists (bench only), keep base-fail ∧ gold-pass. Render "manager" or
"none" evidence as `unverified` in the receipt rather than as a pass.

**Measure before adopting.** `bench/review_judge` discrimination over fixed cases: how many current
generated tests pass pre-patch (predicted: many). US$2–5.

### A4. A fact recalled from memory does not taint the run that uses it

**Paper.** 2607.05029 (slice 07): poison that enters through memory recall bypasses every
input-side defence, because recall is not treated as a fetch.

**Per slice report (not re-verified today).** `record_fetch` is called for web/file reads, not for
`memory recall`. **Change.** `record_fetch("memory:<id>")` on recall of any item whose origin was an
untrusted fetch. **Measure.** `bench/memory_poison` already exists: add a row where the poison enters
via recall and a tool runs afterwards; the taint-narrowing path must fire. US$0.5.

### A5. Eleven copies of `_strip_fence`

**Paper.** 2609.06993 (slice 14) — duplicated parsers drift. **Verified:** 11 occurrences across
`core/checklist.py`, `orchestration/draft.py`, `core/ledger.py`, `core/spec_test.py`,
`governance/quarantine.py`. One helper. Hours, no measurement needed. Lowest priority; listed so it
is not rediscovered.

---

## B. Desktop app (approval card, pending chip, Orchestration tab)

### A6. Trust studies that score self-report find nothing; score comprehension

**Papers.** 2609.09713 (slice 15, RCT 2×2, n=270): showing three functions raised perceived
multifunctionality; **no other main effect survived correction across six outcomes; a rationale did
not reliably raise perceived intelligence**; ability, integrity, benevolence move separately.
2609.07607 (N=16): source-linked reading was faster and *felt* clearer — **rubric-scored
comprehension did not differ**. 2609.05563 (N=16): inline execution raised confidence, not
correctness.

**What it changes.** The approval card work in A1 must be scored by an objective question — "what
will this command do, and who wrote it?" answered correctly — never by "did you feel informed". A
desktop study that asks the second question will report a positive that the first would not.
`bench/right_hand_governance` gets a person-arm protocol with a comprehension probe. US$0 (it is a
protocol), and it decides what A1's acceptance test *is*.

### A7. The pending queue is oldest-first; coverage, not judgment, is what a person loses under load

**Paper.** 2608.06949 (slice 16, code at github.com/Polpii/policy-town): 192 episodes / 2,304 paired
cases; single vs nine-agent pipeline **no difference** in biased outcomes (6.9% vs 6.1%, p=0.498);
what moved was **coverage** — 100% → 65.6% under an overloaded auditor (p<0.001) while judgment on
reviewed cases stayed flat (81.6% vs 85.7%, p=1.000); a **risk-ordered queue** recovered coverage
65.6% → 91.7% (p=0.028).

**Verified here.** `chimera/governance/pending.py:122` — `pending()` returns `sorted(out, key=asked_at)`;
the kernel's allow/warn/review/block level is not on the record. 5/8 legitimate rows become
questions (measured in #418) and silence refuses: that is coverage loss with a human as the auditor.

**Change.** Carry the kernel verdict and rule on `PendingApproval`; sort the queue and the desktop
pending chip by tier, then age; `answer_stats` (PR #424) reports answered/timed-out **by tier**.

**Measure.** Simulated answerer with a capacity limit over the recorded rows: coverage of `block`-tier
questions FIFO vs risk-ordered. US$0. Live numbers wait on the VPS approval log, which stays empty
while `CHIMERA_GOVERNANCE` is off there (Bruno's decision, 2026-08-14 and 2026-09-11).

### A8. Before the Orchestration tab ships: the equal-call arm

**Papers.** 2609.04217 (slice 16): fix total LLM calls; Planner-Executor-Critic vs a single frozen
agent — ALFWorld team 0.769 vs single 0.754, **p=0.80**, with 1.8× more evaluation calls; leave-one-in
shows all realized value is the executor's; planner and critic prompts evolve to empty. SimTIO
2609.05740 (slice 15): LLM specialists vs grounded random search at an **equal seven-simulation
budget** — Top-10 differences not significant. 2609.04898 (slice 14): single agent with retrieval >
sub-agents on code. 2609.07680 corroborates from the other side: an audit layer that relays does
not check (see C1).

**Verified here (per slice 16, consistent with `bench/hierarchy*` READMEs).** `bench/hierarchy` and
`bench/hierarchy_multistep` are paired and token-honest (+47% tokens single-shot; −66.5% multi-step)
but never fix the **call count**, and have no per-role ablation. The tab plan
(`~/.claude/plans/mutable-weaving-thompson.md`) routes `sequential_write` to `IsolatedCrew` (N
attempts, `verify` picks) — that arm has never been measured against one agent given the same N calls.

**Change.** `bench/hierarchy_multistep/run.py`: an equal-call-budget arm and leave-one-in (drop
verifier / drop synthesizer). The tab's plan preview should print the counterfactual it was measured
against, not only tokens.

**Measure before shipping the tab.** US$5–10 on the existing task set, three seeds.
**Cannot show:** anything about crews on tasks the bench does not contain.

### A9. Crew diff capture — already in the tab plan, now with a paper

2609.10123 (slice 13): patch oscillation is the stagnation signature of multi-attempt repair; two
tied winners fuse to zero under one-file-one-owner (`isolation.py:164`). The plan's "capture each
worker's diff before cleanup" item is mandatory, not polish.

---

## C. Core and bench (fusion, orchestration verify, memory, evolution, protocol)

### C1. The envelope spot-check hands the auditor the worker's conclusion — measured at 4.1% accuracy

**Paper.** 2609.07680 (*Audit Without Verification*, slice 16; pre-registered on OSF; code at
github.com/Polpii/FaultLine). 6-agent pipeline, 345,600 requests per chain model. An auditor reading
agent reports adopts the report's conclusion instead of checking its observations: recovers the
true fault origin in **4.1%** from reports vs **60.3%** from raw documentation of the same episodes;
**deleting the conclusion clause: +41.2 pp** (95% CI +35.3 to +46.9), adherence 94.4% → 3.4%; an
instruction to distrust changes nothing; replicates on two frontier auditors 4/4 and a second domain
(+47.7, +61.1 pp). The stated cost: where the relayed conclusion was *correct*, deleting it **costs**
70.5% → 55.7%.

**Verified here.** `chimera/orchestration/envelope_verify.py:44` — the auditor prompt says *"Do NOT
trust the summary — re-derive every judgement from the RAW OUTPUT"*; lines 175–176 hand it
`## Worker summary` **and** `## Raw output`. That is the configuration the paper measures at 94.4%
adherence, instruction included.

**Change.** Two-stage: the auditor extracts findings from the raw output **blind** to the summary;
then a deterministic comparison against the summary (invented / dropped / contradiction — the three
classes `EnvelopeVerifier` already decomposes). Same shape is the narrate-as-success verifier for
`solve`: the verifier reads tool receipts, never the narrative.

**Measure before adopting.** Replay the stored envelope corpus through both arms; the paper predicts
a large gain where the summary is wrong and a loss where it is right, so the net depends on the
worker's error rate — which the corpus gives. Note the spot rate is 0.2 and fires only when
`evidence_refs` exist, so most envelopes are never spot-checked; report that fraction alongside.
US$1–3.

### C2. The fusion judge sees the panel's order and each model's name

**Papers.** 2609.08016 (slice 08): judges prefer the first answer and the larger vendor when told;
blinding removes most of it. 2609.02925 (slice 16, theorems + a frozen 120-task bench): reviewers
sharing one upstream input form **one fault domain** (κ_E = 1) however many there are; adding voters
at fixed threshold cannot raise it. 2609.09989 / 2609.06386 (slice 08): consensus is not safety.
2609.03422 (slice 08): panel diversity measured by NCD of the *answers*, not by lineage.

**Verified here.** `chimera/fusion/engine.py` `_run_judge` (from line 502) formats the panel as
`--- Answer {i} (model {r.model}) ---` in arrival order. `panel_diversity()` (line 99) and the
`independent` flag (line 200) measure **lineage** independence; every panelist reads the same prompt
and the same fetched page.

**Change.** Blind labels (A/B/C), order shuffled per call, model names withheld from the judge and
synthesizer; the permutation goes in the trace so the receipt still attributes. An `input fault
domain` flag on the fusion receipt when the panel's context contains a tainted fetch.

**Measure before adopting.** `bench/fusion_paired` re-run with blinded vs named judge: rate at which
the judge picks position 1 and the rate at which it picks the largest model, against chance; a fusion
arm in `bench/injection` to test whether panel agreement masks a poisoned page. US$3–8. This one
touches VPS production (fusion is on for decisions), so it ships behind a flag with the number.

### C3. The memory eviction policy has never been measured, and the paper predicts ~0%

**Paper.** 2609.05767 (slice 15): published eviction benchmarks compress a prompt that already
contains the future question; hide it and the benefit vanishes. PA-Bench, n=100: an allergy mentioned
in passing survives to the question that needs it **0–1%** at a 20% budget vs **97%** with full
memory; "the cause is the budget, not the scorer".

**Verified here.** `chimera/memory/manager.py:153` — `prune(max_items, dry_run=)` (the slice report
said `value.py`; that file holds only the weights: `chimera/memory/value.py:16`, episodic 0.5 /
working 0.2, specificity = len/200 — precisely the profile the paper says is evicted first).
**No caller in `bench/` or `chimera/eval/`**; `tests/test_memory.py` tests correctness, not
contribution. `bench/compaction` hides the probe but tests rules, not in-passing facts, and
compaction fired in 0/137 runs.

**Change.** None yet — measure first. **Measure.** `bench/memory_prune/`, deterministic and offline:
one short safety fact + N distractors, `prune` to 20%, probe hidden until after; report survival.
Predicted near 0. Companion slices from MERIT 2609.05441 (updated-fact: does the *old* value leak?),
Revoked 2609.08258 (revoked fact still surfaces?), 2609.05339 (`consolidate` deletes originals —
the leak floor). US$0.

### C4. The evolution acceptance gate declines the bound the field now uses

**Papers.** PACE 2606.08106 (slice 06): anytime-valid acceptance for a self-improvement loop that
peeks — the fixed-sample Wilson bound accepts on noise when the loop checks repeatedly. GRASP
2605.29668 (code): a **regression budget** per accept, not a single pass/fail. VaG 2608.05810: a
pre-commit gate that runs the candidate on held-out tasks before it replaces the incumbent.
2609.02889: value lives in the reflection/control slot; budget floor 16–32 evaluations vs `evolve
tune`'s 20. AhaBench 2609.05435 (code) and 2609.09468: a continuous metric, not pass/fail.

**Verified here.** `chimera/eval/anytime.py` docstring: *"Deliberately NOT the paper's anytime-valid
confidence sequences … those are for a continuous-peeking accept loop Chimera does not run"*. The
learning-lift series (7 pre-registered runs) is exactly a repeated-peek loop, and its one positive
was retracted at the next run.

**Change.** An anytime-valid variant behind the same interface; a regression budget in the accept
decision. **Measure.** Replay the seven learning-lift logs through both gates offline: how many
accept decisions flip. US$0. **Cannot show:** whether learning helps — the series' standing null
stands (FinSkillBench 2608.18099 is an external null on skills; 2606.20615 measures prose skills at
0 and *enforced* checks at +14–22, which says the skill card as text is the wrong object and the
`Check` as a gate is the right one).

### C5. Narrate-as-success: replace the regex with a fault injector and state assertions

**Papers.** 2609.00038 (slice 02, code): a fault injector that corrupts tool returns and scores
whether the agent's report reflects the corruption. 2609.05587: the `corrupted_return` family.
2609.04706: state assertions and twins instead of output regex.

**What exists.** `bench/refusal_shape` (2026-09-11): 0/382 narrations on the default model across
four refusal shapes — and its `claims` regex was found broken (matched negations); the hand count is
the result. `bench/scenarios` v3 pre-registration. **Change.** A `corrupted_return` arm in the
scenarios runner scored by post-state assertion. US$1–5.

### C6. Cascade bench confounds the gate with the tier

**Papers.** 2609.04040 (slice 15): the same admission gate on a 257-parameter MLP and a stride
predictor — the neural advantage vanishes; the gate is the effect; gate-closed reproduces the
baseline exactly; proxy accuracy 11→15% while the endpoint moved **0.07%**. 2609.01345 / 2609.05274
(slice 08): off-gate accepted-wrong rate; a draft-model gate at AUROC 0.77 costs −5 pp.

**Verified here.** `chimera/eval/cascade_bench.py:26` — `ARMS = ("weak", "mid", "cascade", "fusion")`;
`bench/cascade/RESULTS.md` (n=12) has one confident-wrong pass.

**Change.** Add `weak+gate` (no escalation) and `mid+gate` arms; identity guard "gate always-closed ==
weak-only"; score tokens-per-pass, not gate accuracy. US$2–4.

### C7. Protocol rules for `bench/` — six, from six papers, US$0

- **Probe the wall before scoring** (SaltBench 2609.11076): isolation is assumed in every bench;
  add a pre-scored breach probe to `bench/harness_bench/run.py`.
- **A budget stop is a halt, never a failure** (same paper): `chimera/eval/replicated.py` has no
  halt class; the learning-lift 7a lesson (a swallowed timeout read as capability loss) is this rule
  unwritten.
- **Prefix cache moves cost 36–75%** (2609.04748, slice 10): the local-bench protocol must fix
  cache state or report it.
- **Interface censoring** (2609.03966): a preflight in `doctor`/`selftest` that the provider
  actually returns tool calls in the shape the adapter parses — our tool benches measured the adapter
  (study 16) and this is the guard.
- **Judge D+1 replay and a paraphrase floor** (2609.04198, 2609.09703): `bench/review_judge` reports
  a judge's self-agreement a day later and under paraphrase before its verdicts are read.
- **Placebo / labels-only arm** (2606.06454): any intervention that adds text to a prompt gets an arm
  with equally long irrelevant text.

Add to `bench/PROTOCOL.md` (create; `PREREGISTRATION.md` files reference it).

### C8. Per-task privilege scope, attenuated to workers — parked, with the caution written down

CAPMAS 2609.06500 (slice 16): map the request to a bounded privilege set before execution, attenuate
across agents; >90% perfect bundle retrieval, 99.5% fewer unnecessary privileges. **Exists:**
allow/deny lists, taint narrowing; crews build a registry per worktree but nothing attenuates it from
the parent's grant (`crew.py`). **Why parked:** the ~10% imperfect retrieval is exactly the
over-refusal channel that #418 just paid for (5/8 legitimate rows refused), and the paper reports no
false-negative rate on legitimate tasks. Revisit after A1/A7 give a coverage number.

---

## D. Nulls worth keeping (so nobody builds the thing)

| paper | what it says | what it stops |
|---|---|---|
| 2609.04217 | multi-agent = single agent at equal calls, p=0.80; planner/critic evolve to empty | shipping the Orchestration tab on the token number alone (A8) |
| 2609.11108 | 100 memory-equipped agents, 26 weeks: swapping the LLM moves every outcome (p=0.0039); **deleting memory moves none**; a tool failing 94–97% of calls is still called at the same rate | "memory helps" as a premise; and it is problem #2 in another substrate |
| 2609.07680 | its own pre-registered hypothesis not supported; deleting the relayed conclusion *costs* when upstream is right | a blind auditor as a free lunch — measure the worker error rate first (C1) |
| 2608.06949 | pipeline vs single agent, no difference; overload degrades coverage, not judgment | adding review stages instead of ordering the queue (A7) |
| 2609.09713 / 2609.07607 / 2609.05563 | rationale text does not raise perceived intelligence; self-report up, comprehension unchanged | any desktop study scored by feeling (A6) |
| 2609.11076 | spec-and-verify costs 1.0–2.89× on every component; sign test k=4 reaches no verdict | "verification is cheap" as an assumption |
| 2609.04040 | proxy 11→15% while endpoint moved 0.07% | reading gate accuracy as cascade benefit (C6) |
| 2608.18099 FinSkillBench | external null on skill accumulation | reopening the learning-lift series without the enforced-check object (C4) |
| 2609.04535 | a CodeQL FP refinement removing 15.8% of paths also dropped 1 of 8 true positives | a SAST gate in the verify phase without a triage-cost line |
| 2609.09242 | a wrong skill card does measurable damage | skill cards on by default without a regression budget |
| 2609.03192 | two pre-registered predictions refuted in opposite directions; **no false completion accepted across 2,581 substituted-panel claims** once the institution adjudicates | — corroborates verify-or-revert; the trusted-falsehood cost (~900 futile actions per run) is problem #7 in numbers |

---

## E. Already here — no work

Hash-chained audit log; durable ask with `history.jsonl` + `answer_stats` (#424); verify-or-revert
(`chimera/core/autonomous.py`); pre-registration and sabotage-verify in every bench; `panel_diversity`
and the `independent` judge flag (lineage — C2 adds the input axis); Wilson/Newcombe bounds
(`anytime.py`, fixed-sample — C4 adds the peeking variant); taint narrowing at the tool boundary;
`replicated.py` with `pass^k` and mechanism-active scoring (study 16); the tool-defer, eval-awareness,
retry-contamination and compaction benches that several slices proposed from scratch.

---

## F. Skipped clusters (do not re-read)

Control/power/robotics (eess.SY ~170), wireless and information theory (cs.IT ~120), accelerators
and serving silicon (cs.AR ~30), logic/type theory/automata (cs.LO/cs.PL ~45), non-AI HCI (XR,
haptics, EEG, health, education ~40), multimedia generation (cs.MM ~25), systems performance below
the substrate (cs.OS/cs.MS/cs.PF ~20), domain agent frameworks with no transferable measurement
(organoids, clinical cases, digital twins, film, forecasting ~20), diffusion/vision/speech self-
improvement (~600 across the search pages: the mechanism is training-time, the substrate is not code),
RL-for-math self-improvement without a verifier we have (~300), survey/position papers without a
number (~150).

---

## H. What happened (added 2026-09-11, end of day)

All nine items above shipped the same day as PRs #425–#433, each with its own pre-registration,
measurement and RESULTS.md. The headline numbers, in the order the plan gave them:

| item | PR | measured |
|---|---|---|
| C1 blind auditor | #433 | the weak-tier auditor said DROPPED on **4 of 23** summaries that had cut the critical finding; the blind form on 19 of 23 with false alarms +35–48 pp → shipped as a **recovery** (absent findings appended to the summary), not a gate |
| C2 blinded judge | #432 | 240/240 named vs 119/120 blind — a ceiling: the reasoning judge re-derives GSM8K; blinding costs nothing and is now the default |
| A1 + A7 | #425 | 12 rows: distinct question strings 6 → 12, questions naming the read 0 → 12, empty actions 12 → 0; queue by level; answer rate per level |
| C3 memory_prune | #426 | 7 of 144 profiles survive a 20% budget, all "written last + semantic + keyed"; written first 0/48 — position decides |
| A2 package_install | #427 | REVIEW on installs by name; the lockfile shapes untouched |
| A3 spec-test base gate | #431 | 58 of 122 generated tests pass on the buggy base (48%); vacuous tests excluded, abstain when none discriminate |
| A8 equal-call arm | #430 | 3B backbone: hierarchy 0.27 vs one agent at equal calls 0.60 vs workers alone 0.57 — the synthesiser loses the needles; 8B run void (ceiling) |
| C4 error budget | #428 | Bonferroni across rounds; null promotion ~20% → <3% simulated |
| C7 protocol | #429 | `ReplicatedArm.halted`; `bench/PROTOCOL.md` |

Cost of every live measurement together: about **US$ 6**. Three predictions were wrong and are
published as such (C3's axis, A3's all-vacuous rate, A8's re-reading); two instruments hit a
ceiling and say so (C2, A8's first run).

## G. Suggested order

| # | item | surface | model cost | decides |
|---|---|---|---|---|
| 1 | C1 blind auditor replay | core | US$1–3 | whether `_spot_check` is measuring anything |
| 2 | C2 blinded judge | core / VPS | US$3–8 | a production bias in fusion |
| 3 | A1 + A7 question content + risk-ordered queue | terminal + desktop | US$0 | the task chip; coverage by tier |
| 4 | C3 `bench/memory_prune` | core | US$0 | whether `prune` keeps a safety fact |
| 5 | A2 package-install rule | terminal + desktop | US$0.5 | an open hole |
| 6 | A3 base-fail gate on generated tests | terminal | US$2–5 | whether `solve` verification discriminates |
| 7 | A8 equal-call arm | desktop (tab prerequisite) | US$5–10 | whether the Orchestration tab has a number |
| 8 | C4 anytime-valid replay | core | US$0 | how many learning-lift accepts were noise |
| 9 | C7 protocol rules | bench | US$0 | the ruler, before the next measurement |
| 10 | C5, C6, A4, A5, A9 | mixed | ≤US$10 | follow-ups |

Total for 1–9: **≈US$15–30 and about two weeks.** Nothing above ships without its measurement,
and every measurement is pre-registered with the number the paper predicts written first.
