# Pre-registration — does accumulated learning actually help?

**Committed before any model call.** Design, task order, metric, predictions and the null-handling
rule are fixed here first, so no choice below can be made after seeing a number.

## The gap this closes

Chimera calls itself self-evolving. It has the machinery — learned skills gated on recurrence + a
transfer test + a governance validator, anti-pattern cards distilled from recurring failures, a
persistent memory with provenance, a diff-gate that refuses to count a step whose real working-tree
diff changed nothing.

**None of it is measured.** `chimera/eval/continuous.py` measures whether performance *holds* across
chained tasks (the anti-degradation proof) — not whether it *improves*. And the headline weak-model
lift benchmark disables learning outright in both arms:

```python
_HYGIENE = ["--no-remember", "--no-collect", "--no-evolve-skills"]   # bench/local_lift/run_paired.py
```

So the project's flagship number measures the **scaffold within one task**, and says nothing about
accumulation across tasks. That is an honest experiment; it is just not this one. This bench is the
missing symmetric half: with learning ON and carried forward, does the agent get better as it goes?

## Why this task suite

Learning is only *measurable* where transfer is *possible*. Of the 100 tasks in `bench/local_lift`,
only one family has more than two members: **`fix_*`, 30 tasks**, all sharing one shape — *"the
package `X` has a bug: `FUNC` in `X/file.py` does Y instead of Z; fix it"*, one file each.

Running this on the full 100 would very likely produce a null **by construction** — 70 one-off tasks
with nothing to transfer between them — and a null like that is uninformative, because it cannot
distinguish "learning does not work" from "there was nothing to learn". Choosing the family with real
shared structure is what makes a null result mean something. It also makes this the *friendliest*
suite for the hypothesis, which is stated plainly here so a positive result is read with that in mind.

## Design

Two arms over the **same 30 tasks in the same fixed order** (`tasks.py` order, committed).

| | Arm | Learning |
|---|---|---|
| **A** | `cold` | `--no-evolve-skills --no-remember --no-collect`, fresh `CHIMERA_HOME` **per task** |
| **B** | `learning` | evolution + memory ON, **one `CHIMERA_HOME` carried across all 30 tasks in order** |

Everything else is identical: same model, same timeout, same scaffolding flags, same fresh workspace
per task, same grader.

### The metric — difference-in-differences

Comparing arm B's second half to its first half alone would confound learning with the tasks'
intrinsic difficulty ordering. So the estimate is a **DiD**:

```
learning effect = (B_second − B_first) − (A_second − A_first)
```

Arm A's half-to-half difference is the drift caused by task ordering and noise with no learning
present. Subtracting it leaves the part attributable to accumulation. First half = tasks 1–15,
second half = 16–30, split by the committed order.

Reported alongside: per-arm overall pass rate, per-half rates, and the paired per-task grid.

### Grading

The hardened grader from `run_paired.py`: the pristine test is restored before every verdict, and any
arm that modified its own test is recorded and its workspace preserved. Solve still receives the test
as its `--verify` gate — that is the regime being measured — but it cannot grade itself.

## Pre-registered predictions

1. **Primary:** DiD > 0 (arm B improves more across halves than arm A).
2. **Skills actually learned in arm B > 0.** This is a *validity check, not a result*: if arm B
   finishes having learned nothing, the experiment measured nothing, and it will be reported as
   **"no learning occurred to measure"** — never as evidence that learning does not help. This is a
   live possibility: two of the acceptance gates were found broken during the audit that motivated
   this bench (the strict mode could not accept any result at all; the default mode over-accepted).
3. **Cost:** arm B spends more tokens than arm A (proposing and testing skills costs calls). Any lift
   has to be read against that, so both arms' token spend is recorded.

## Power — stated before the run, not after

n=30 split into halves is **small**. With 15 tasks per half per arm, only a large effect will clear
zero. A null result here means **underpowered**, not **no effect**, and will be labelled that way.
Anyone who reads a null from this as "learning is useless" is reading it wrong, and this paragraph
exists so that reading cannot be retrofitted.

## Rules that bind this run

- **One run.** No re-rolls. The first complete result is the result.
- **No post-hoc exclusion.** Every task in the committed order counts, including ones both arms fail.
- **A null ships.** If DiD ≤ 0, it is published with the same prominence as a win — the project's
  Terminal-Bench null is the precedent.
- **Order is fixed here.** Not re-shuffled after seeing outcomes.

## What this cannot answer

One model, one seed per cell, one task family, 30 tasks. It cannot say whether learning helps on
real-world repos, on other families, or at larger n. It is a first measurement of a quantity that
currently has none.

---

# Amendment — run 2, on a harder suite

**Committed before any model call of run 2.** Run 1 is finished, published, and is not touched by
this amendment. Its numbers stand as reported.

## Why a second run at all

Run 1 returned a null, and the null was **uninformative for a reason visible in its own data**: arm A
(cold, no learning) passed **15/15 on the first half**. A control arm at the ceiling cannot get
better, so the DiD had no room to move regardless of whether learning works. That is a defect of the
*suite*, not of the hypothesis and not of the agent.

The tension was structural and is worth stating plainly, because it is why run 1 could not win:

| subset of the 100-task suite | n | raw model | full scaffold (= arm A here) |
|---|---:|---:|---:|
| `fix_*` — the only family with shared structure | 30 | 67% | **93%** ← ceiling |
| non-`fix` one-offs — right difficulty | 70 | 40% | **61%** ← nothing to transfer |

The tasks with transfer were too easy; the tasks at the right difficulty had no transfer. Run 2
removes that trade-off by **authoring a new family that has both**.

## The new suite

`bench/learning_lift/tasks_hard_fix.py` — **40 tasks**, same `fix_*` shape (one buggy module, one
pytest file), so transfer between them remains possible.

**Difficulty target, fixed before authoring and recorded in that file's docstring: the control arm
lands at 40–60%.** Four inversions were applied to every task to get there, chosen a priori:

1. the prompt states the **contract**, never the failing symptom;
2. the bug is not on the line the prompt points at;
3. the obvious patch fixes the visible case and **breaks a second one** the test also checks;
4. one clause of the contract is quiet — stated once in prose, enforced by an assertion.

**The tasks were not tuned against measured outcomes.** They were authored to the spec above, then
validated once — mechanically, with no model involved — for a single property: *the committed test
must fail against the committed buggy source*. 40/40 do. No task was rewritten, dropped, softened or
hardened on the basis of any pass rate, because no pass rate exists yet.

## What changes, and what does not

| | run 1 | run 2 |
|---|---|---|
| tasks | 30, borrowed from `local_lift` | **40, authored for this bench** |
| halves | 15 / 15 | **20 / 20** |
| arms, metric, grader, flags, order rule | — | **identical, unchanged** |

Everything not in that table is the same run-1 protocol: same two arms, same DiD
`(B₂−B₁) − (A₂−A₁)`, same hardened grader restoring the pristine test before every verdict, same
fixed committed order, same token accounting.

## Additional pre-registered rules for run 2

- **Predictions 1–3 above carry over unchanged.**
- **The difficulty target is a description, not a gate.** If arm A lands outside 40–60%, the run is
  still reported, with the realised rate stated up front and the miss named. The suite is **not**
  re-authored to hit the band — that would be tuning the instrument to the answer.
- **Still one run.** No re-rolls, no post-hoc exclusion, and a null still ships.
- **A ceiling or floor is a null of the same kind as run 1's.** If arm A's first half is ≥90% or
  ≤10%, the result is labelled *uninformative by construction* and the DiD is reported but not
  interpreted.
- **n is still small.** 20 per half per arm is better than 15 and still underpowered for anything but
  a large effect. A null means underpowered.

## What run 2 still cannot answer

Whether learning helps outside this authored family, on real repos, or with a stronger model. The
tasks are synthetic and written by the same party running the experiment — the mitigation is that the
difficulty spec and the no-tuning rule were fixed in writing before the first model call, and this
paragraph is the disclosure that the mitigation is not the same thing as independence.

---

# Amendment — run 3, the same suite with the learn→use loop CONNECTED

**Committed before any model call of run 3.** Runs 1 and 2 are finished and are not touched; their
numbers stand. Run 3 is a new run on the **same 40-task hard suite, same committed order**.

## Why run 3 exists — run 2's null was a cut wire, not a verdict

Run 2 returned DiD −5.0% with **39 skills minted**. A grounded 29-agent study of the code
(`LEARNING_ROADMAP.md`) found why the artifacts changed nothing: **the learn→use loop is disconnected
by default.** Skill cards inject only when `settings.skill_cards` is true — it defaults **False**
(`chimera/config.py:199`) — `chimera solve` exposed no flag to turn it on, and the bench set no env,
so `cards=None → card_ctx=''` (`chimera/core/autonomous.py:291`). The ACE playbook injects only with
`--playbook`, which the scaffold never passed. **So run 2 minted 39 skills and injected zero of
them.** Its DiD measured a learning loop with the wire cut; a null was the only possible honest read
of skill accumulation, regardless of skill quality.

## The single change in run 3

The learning arm now carries **`--playbook`** (injects curated cross-task strategy, ungated) and
**`--skill-cards`** (reads learned skill cards back into context). That is the whole change. A new
`--skill-cards/--no-skill-cards` option was added to `chimera solve` to override `settings.skill_cards`
per run **without flipping the global default** (the default stays off — the prior curated card A/B
showed +300% tokens for non-significant accuracy, and flipping it globally is out of scope here).

| | run 2 | run 3 |
|---|---|---|
| learn→use loop in the learning arm | **disconnected** (cards off, playbook off) | **connected** (`--playbook --skill-cards`) |
| tasks / halves / order | 40 / 20-20 / committed | **identical** |
| arms / DiD metric / hardened grader / grading integrity rule | — | **identical** |
| control (`cold`) arm | fresh home, no write | **unchanged** — still no write, no carry, no read |

The read flags are on the **learning arm only** — on `cold` they would be pure no-ops (its
fresh-home-per-task carries nothing to inject) and `--playbook` would waste a curation call on all 40
control tasks. Because the flags can only act **through survived state**, of which `cold` has none,
this keeps the contrast exactly "does accumulated, now-readable state help?". `BENCH_CONNECT=0`
reproduces run 2's disconnected arms from the same code.

## New pre-registered rule specific to run 3 — the connection check

Because "connected" is the entire hypothesis, the run **verifies the wire is live** and reports it:

- **`skill_card_uses` (credited only when a card actually reached a prompt on a verified run) and the
  end-of-run playbook bullet count are logged.** If **both are zero**, the loop did **not** connect and
  the result is labelled the *disconnected null* (same status as run 2), **not** a transfer result.
- **Per-card attribution (`uses`/`successes`/`rate`) is reported**, so a connected-but-still-null DiD
  is diagnosable: `uses>0, rate low` = retrieved but did not transfer; `uses=0` = never retrieved.
- **This check is a validity gate, not a success criterion.** A live wire with a null DiD is a real,
  reportable outcome (it motivates the Level-2 content proposals P3/P4 in `LEARNING_ROADMAP.md`).

## What carries over unchanged

- **Predictions 1–3 and the power caveat carry over.** n=40 in halves of 20 is still small; a null is
  underpowered, not "no effect". Single seed per cell; the −5pp of run 2 sat inside that noise and a
  new small DiD will too — a claimed effect must survive multiple seeds (not run here).
- **The ceiling/floor rule carries over.** If `cold`'s first half is ≥90% or ≤10%, uninformative by
  construction; DiD reported, not interpreted.
- **Still one run.** No re-rolls, no post-hoc exclusion, a null ships with equal prominence.
- **Token accounting remains not captured** (as in runs 1–2); the connection is asserted via
  `skill_card_uses`/playbook growth, not token deltas.

## What run 3 still cannot answer

Whether a connected loop helps at adequate power, on real repos, with a stronger model, or with the
richer error-seeded content of P3/P4. Run 3 answers exactly one question: **with the wire connected,
does the counted learning move the number on this suite?** — and, if not, **which failure mode**
(never-retrieved vs retrieved-but-no-transfer) the attribution log points to.

---

# Amendment — run 4, the right meter (pooled paired) at power, + P3 error-seeded curation

**Committed before any model call of run 4.** Runs 1–3 stand unchanged.

## Why run 4, and the honest metric change

Run 3 **connected the loop** (validity check green: 35 skill-card retrievals credited, 50 playbook
bullets — run 2 was 0 by construction). Yet the DiD stayed ~0 **while the learning arm sat +10pp
above cold in BOTH halves** (55 vs 45, 85 vs 75). That pattern is a **level shift**, and the DiD is a
**slope** estimator — it asks whether the second half improves *more* than the first, and subtracts
any constant offset to zero. A connected loop whose useful bullets are learned early and then help
roughly equally is exactly a level shift the DiD cannot see.

So run 4's **primary meter is the pooled paired estimate**, not the DiD:

- **Paired, not unpaired:** both arms solve the SAME task from an identical fresh workspace, so each
  task is a matched pair. McNemar + a Wilson interval on the discordant pairs
  (`chimera/eval/paired.py`) gives a difference CI that **can** detect a constant offset and is
  tighter than an unpaired interval on the same data.
- **Pooled across seeds for power:** `BENCH_SEEDS=3` runs the whole cold+learning suite 3×; the model
  is sampled with temperature>0 (runs 2 vs 3 already differ on identical tasks), so the 3 repetitions
  are independent draws and their per-task pairs pool to **n = 120 paired trials**.
- **The DiD is still reported** (per seed + mean) for continuity with runs 1–3.

**Integrity — is this moving the goalposts?** Stated plainly so it can't be retrofitted: the paired /
overall-rate comparison was **already named in the original design section** ("Reported alongside:
per-arm overall pass rate … and the paired per-task grid") as a reported secondary. Promoting it to
primary is a **disclosed methodological update with a structural reason** (a level shift is invisible
to a slope estimator) — not "it gave a nicer number". AND, less comfortably: run 4 is a
**confirmation attempt of a run-3 observation** (the +10pp), which is epistemically weaker than a
virgin pre-registration. If the run-3 +10pp was noise, run 4 with 3× the sample should fail to clear
zero; that is the test, and a null is the honest possible outcome.

## The other change in run 4 — P3 (error-seeded playbook curation)

The ACE playbook curator was **blind to why a task failed** — it received only verdict + final
answer (`chimera/cli/main.py`). P3 feeds it the real error evidence already in `result.attempts`: the
failing verifier output from the last failed attempt and the diff that ultimately passed, so it
distils **process pitfalls** ("run the given test first", "re-check a second case", "re-read the
docstring for quiet clauses") instead of platitudes. On by product default
(`CHIMERA_PLAYBOOK_CURATE_FROM_ERRORS`, default True).

**Bundling disclosure:** run 4 therefore measures **P1+P5+P3 together**. It **cannot** attribute any
lift to P3 alone — the isolated ablation (`CHIMERA_PLAYBOOK_CURATE_FROM_ERRORS=0`) is future work.
What run 4 tests is whether the **best connected+seeded loop** beats the no-learning control at power.

## What changes, and what does not

| | run 3 | run 4 |
|---|---|---|
| primary meter | DiD (slope) | **pooled paired delta + 95% CI** (McNemar/Wilson) |
| seeds | 1 | **3 (pooled → n=120 paired trials)** |
| playbook curation | verdict-only | **error-seeded (P3)** |
| arms, suite, order, grader, connection check | — | **identical** |

## Pre-registered predictions for run 4

1. **Primary:** pooled paired delta (learning − cold) **> 0 with 95% CI excluding 0** (learning
   significantly above control). This is the confirmation test of the run-3 +10pp level shift.
2. **Secondary:** the DiD stays ~0 (the benefit is a level, not an accelerating slope).
3. **Validity:** the connection check stays green (skill-card uses > 0 and/or playbook bullets grow).
   If it goes zero, the run is the disconnected null, not a transfer result.

## Rules that carry over

- **One run** = the single 3-seed invocation. No re-rolls, no post-hoc exclusion, a null ships with
  equal prominence.
- **Underpowered ≠ no effect.** 120 paired trials beats 40 but a ~10pp effect can still fail to clear
  zero; a not-significant CI is labelled underpowered. **More seeds can be added** (cost is ~$0.25 per
  full run — money is not the constraint; wall-clock is) if a follow-up is warranted.
- **Ceiling/floor rule** carries over on the pooled cold rate.
- **Grading integrity** gate carries over (any arm that edits its own test is recorded, workspace
  preserved).
- **Token accounting still not captured**; the connection is asserted via `skill_card_uses`/bullets.

---

# Amendment — run 5, semantic recall: the retrieval-quality test

**Committed before any model call of run 5.** Runs 1–4 stand unchanged.

## Why run 5

Run 4 was a well-powered null: connected + error-seeded, n=120 paired, Δ −0.8% [−8.7%, +7.2%]. The
leading explanation for *why* the injected learning didn't help is retrieval quality: cards (BM25) and
memory facts (keyword) are matched **lexically**, so on a surface-disjoint suite the injected
"neighbours" rarely share meaning with the task. A separate measurement makes that concrete —
`memory_bench` (paraphrase corpus): **keyword paraphrase recall 0%, semantic 94%** (a +0.938 gap,
`text-embedding-3-small`, dim 1536). Semantic recall bridges exactly the synonyms lexical retrieval
cannot. Run 5 asks the decisive question: **with relevant (semantic) neighbours, does the connected
loop finally move?**

## The single change

The **learning arm** sets `CHIMERA_SEMANTIC_MEMORY=1` (bench flag `BENCH_SEMANTIC=1`), so BOTH memory
facts and skill cards are retrieved by **embedding cosine** instead of keyword/BM25 (new semantic path
in `CardRetriever`, gated by the same flag the memory-fact recall already used). Everything else is
run 4: connected loop (`--playbook --skill-cards`) + P3 error-seeded curation, the **pooled paired**
estimator, **3 seeds → n=120**, same suite, same committed order, same hardened grader. `cold` is
untouched — it carries no facts/cards, so semantic there is a no-op, keeping it a true no-learning
control.

## Pre-registered predictions — and the two distinct null-meanings

1. **Primary:** pooled paired Δ (learning − cold) **> 0 with 95% CI excluding 0**. A positive move means
   **retrieval quality was the blocker** — relevant neighbours are what the loop needed.
2. **If still null**, it is pre-committed to mean something specific and different from run 4:
   **relevant retrieval is necessary but not sufficient on this suite** — the ceiling is
   *transfer-poverty* (few genuinely similar past TASKS exist regardless of how well retrieval finds
   them, because the suite is disjoint by construction), which points the next effort at a
   recurring-pattern / real-repo suite, not at more retrieval work. Run 5 thus separates
   "retrieval quality" from "nothing to retrieve" as the cause of the null.

## Honest caveats

- `memory_bench`'s +0.938 gap is on a **clean synonym corpus** favourable to semantic; it does not
  guarantee that real minted cards embed to task-relevant neighbours. Run 5 is the end-to-end test of
  exactly that.
- Embeddings now cost per solve (card docs + query embedded each task) — small, and recorded as a
  behaviour change, not a token-accounted number.
- All run-4 rules carry over: one run (the 3-seed invocation), no re-rolls, a null ships, the
  ceiling/floor and grading-integrity gates apply, connection asserted via card uses / bullets.

---

# Amendment — run 6, a transfer-POSSIBLE suite (recurring families)

**Committed before any model call of run 6.** Runs 1–5 stand unchanged.

## Why run 6

Runs 1–5 nulled and the cause is diagnosed: the `hfix_*` suite is surface-DISJOINT, so there is nothing
to transfer between tasks — run 5 proved even semantic retrieval (which fixes retrieval *quality*) does
not move the number, isolating **transfer-poverty** as the ceiling. Run 6 removes that ceiling by
construction: a suite where solving one task genuinely teaches the next.

## The suite

`bench/learning_lift/tasks_recurring.py` — **25 tasks, 5 families × 5 members**, ordered family-by-
family. Each family shares ONE nameable, transferable fix a distilled card can capture: **guard_**
(empty input → return the default, guard at top), **copy_** (never mutate the input), **incl_** (the
range end is inclusive), **case_** (compare/group case-insensitively), **reset_** (reset the per-group
accumulator). Every task states the CONTRACT not the symptom, so the model must diagnose — but within a
family the diagnosis recurs. Validated mechanically (all 25 committed tests fail against their committed
buggy source), no model involved, no tuning against any pass rate; families/patterns fixed before
authoring.

**This is the *friendliest* possible suite for the hypothesis, stated plainly here so a positive result
is read with that in mind** (same disclosure discipline as run 2). It is engineered so transfer is
possible; that is the point — the disjoint suite was engineered so it was not, and both are honest
instruments for different questions.

## Config — identical to run 5

Connected loop (`--playbook --skill-cards`) + P3 error-seeded curation + **semantic recall on the
learning arm** (`BENCH_SEMANTIC=1`), pooled paired estimator, **3 seeds → n=75 paired trials**, same
hardened grader, `cold` untouched. Only the SUITE changes (`BENCH_SUITE=recurring`).

## Pre-registered predictions

1. **Primary:** pooled paired Δ (learning − cold) **> 0 with 95% CI excluding 0.**
2. **Secondary — the sharp transfer test:** the learning arm only carries a family's card AFTER its
   first member, so if accumulated learning helps at all, the paired lift on the **later members**
   (n=60, 4 per family) must exceed the lift on the **first members** (n=15) — a **positive transfer
   gap** (`later Δ − first Δ`). This is the metric the disjoint suite structurally could not produce.

## The three outcomes, pre-committed

- **Pooled Δ > 0 AND transfer gap > 0** → accumulated learning DOES help when transfer is possible; the
  five prior nulls were the suite, not the machinery. The strongest positive the series can produce.
- **Still null on a transfer-possible suite** → learning does not help even when transfer is available —
  a stronger negative than the disjoint nulls, pointing past retrieval/suite to the loop itself.
- **Transfer gap > 0 but pooled Δ ns** → real but weak/diluted transfer; motivates more power (seeds)
  and the first-vs-later split as the primary lens.

## Caveats (carry over)

n=25 (75 paired trials) is small; a null is underpowered, not "no effect". Authored synthetic families,
one weak 24B model, single seed per cell. One run, no re-rolls, a null ships, ceiling/floor and
grading-integrity gates apply. The families make transfer *possible*, not *guaranteed* — a card must
still be minted and retrieved and obeyed for the lift to appear.

---

# Amendment — run 7, the hard recurring suite (confirm the run-6 signal on the primary metric)

**Committed before any model call of run 7.** Runs 1–6 stand unchanged.

## Why run 7

Run 6 produced the series' first positive: on a transfer-possible suite the pre-registered within-family
transfer metric went positive and significant (later-member paired Δ +6.7% [+0.1%, +6.7%], vs +0.0% on
first members). But two caveats kept it from a closed proof: the *pooled* primary was **ceiling-limited**
(the easy recurring families gave cold 90.7%, bench-flagged uninformative), and the significant signal
rested on **thin discordant counts** (n=75, 6 disagreements). Run 7 attacks both.

## The single change: a harder recurring suite + more seeds

`bench/learning_lift/tasks_recurring_hard.py` — the SAME five families (`hguard_`/`hcopy_`/`hincl_`/
`hcase_`/`hreset_`), but every member rebuilt with the four difficulty inversions of the `hfix_*` suite,
applied **family-specifically so the trap RECURS within a family** (a card learned on member 1 still
transfers): e.g. every `hincl_` member has two inclusive bounds so a single-site patch leaves a second
checked case failing; every `hguard_` member's emptiness arises after a filter so a naive guard breaks a
second case. Difficulty target, fixed before authoring: the control lands **40–60%** (off the run-6
ceiling). Validated mechanically (25/25 committed tests fail against their buggy source), no tuning
against any pass rate. Ordered family-by-family, 5 × 5 = 25 tasks.

**Seeds: 5** (up from run 6's 3) → **n=125 paired trials**, and the harder suite naturally produces more
discordant pairs (arms disagree more away from the ceiling) — directly answering run 6's thin-n caveat.

Everything else = run 6: connected loop + P3 + semantic recall on the learning arm, pooled paired
estimator, hardened grader, cold untouched. Only the suite and seed count change.

## Pre-registered predictions

1. **Primary (the confirmation):** pooled paired Δ (learning − cold) **> 0 with 95% CI excluding 0** —
   this time with the control OFF the ceiling, so a positive would confirm on the primary metric what
   run 6 showed only on the secondary split.
2. **Secondary (transfer, carried over):** the within-family transfer gap stays **positive** — later-
   member paired Δ > first-member paired Δ. Run 6's headline signal must replicate on the harder suite.
3. **Validity:** control first-half in 40–60% (else the bench flags ceiling/floor and the pooled metric
   is reported-not-interpreted, as in runs 1 and 6).

## The pre-committed readings

- **Pooled Δ > 0 significant AND transfer gap > 0** → the run-6 signal is confirmed on the primary
  metric at power: accumulated learning helps when transfer is possible. The strongest close available.
- **Transfer gap > 0 but pooled Δ ns** → the effect is real on the transfer lens but the pooled metric
  is still underpowered; report both, and the family split remains the primary evidence.
- **Both null on a hard transfer-possible suite** → run 6 was a fluke of a ceiling-easy suite; the
  honest retraction, reported with the same prominence.

## Caveats (carry over)

Authored synthetic families, one weak 24B model, single seed per cell. One run, no re-rolls, a null (or
a retraction) ships. n=125 is better than 75 but still modest — a not-significant pooled result is
underpowered, not "no effect". The families make transfer *possible*, not *guaranteed*.

## Run 7a — DISCARDED as infrastructure-contaminated (recorded, not hidden)

The first execution of run 7 was **stopped after 3 of 5 seeds and discarded**. This is recorded here
rather than silently re-run, because an undocumented discard is indistinguishable from a re-roll.

**Evidence it measured infrastructure, not learning** (same 25 tasks, same order, every seed):

| seed | cold | learning | discordant | contiguous learning failures at end |
|---|---:|---:|---|---:|
| 1 | 84% | 84% | L+1 / C+1 | 0 |
| 2 | **96%** | **52%** | L+0 / C+11 | **11** |
| 3 | **52%** | **88%** | L+10 / C+1 | 0 |

The decisive tell is the **`cold` arm**: it carries a fresh home per task and accumulates nothing, so
it should be stable — yet it swung **96% → 52%** across seeds on identical tasks. No capability effect
does that. Seed 2 additionally shows an 11-task *contiguous* learning-arm collapse. Both are the
signature of API-latency variation converting into **silently-swallowed timeouts** (`_solve` suppressed
`TimeoutExpired`, so a timed-out task graded as a plain failure). Per-seed swings of ±36 pp dwarf any
plausible learning effect, so the pooled Δ is uninterpretable.

**This discard follows the rule already pre-committed for the 402 case** ("if it appears, the run is
discarded as contaminated") — the same class of fault: the run measures the harness, not the agent.
No result from run 7a is reported or carried forward.

**Fixes before re-running (run 7b):** (1) `_solve` now returns `(elapsed, timed_out)`, every task line
prints its duration and a `TIMEOUT` marker, a per-arm latency audit reports median/max/timeout-count,
and an **ASYMMETRIC TIMEOUTS** warning declares a run invalid when one arm times out far more than the
other; (2) the per-task budget is raised **240s → 480s**, because the learning arm is structurally
slower (larger prompt + embedding calls + playbook curation) and the tight budget is what converted
latency into fake capability failures. Suite, arms, metrics, seeds and predictions are unchanged.
