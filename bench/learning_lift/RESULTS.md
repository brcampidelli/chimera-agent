# Does accumulated learning actually help? — seven pre-registered runs

Design, task order, metric and predictions were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
**before any model call** of each run, and each amendment was committed before the run it governs. Read
the power caveats there before reading any result below.

**Bottom line up front — including a retraction.** Runs 1–5 nulled on a **surface-disjoint** suite and
diagnosed the cause as **transfer-poverty** (nothing to transfer between unrelated tasks — even semantic
retrieval, proven to bridge paraphrases 0%→94% on `memory_bench`, did not move the number). Run 6 tested
that diagnosis on a **transfer-possible** suite and produced what we reported as the series' first
positive: a significant within-family transfer gap (+6.7%, 95% CI [+0.1%, +6.7%]). **Run 7 was the
pre-registered confirmation of exactly that signal, and it did not replicate.** On a harder recurring
suite at more than 1.5× the sample (n=125, 5 seeds), the transfer gap fell to **+2.0% (not
significant)** and the pooled Δ to **+1.6% (not significant)**. Per the pre-committed reading, that is
**the honest retraction**: run 6's positive is best explained as a small-sample fluctuation at a
ceiling (it rested on 6 discordant pairs with the control at 90.7%).

What survives is weaker and worth stating precisely: across both recurring runs the direction is
**consistently positive but never significant at adequate power** (+5.3%/+6.7% in run 6; +1.6%/+2.0% in
run 7). And run 7 **also failed its difficulty target** — the control landed at 88.8% against a
pre-registered 40–60% — so this is not a clean negative either, but an **underpowered null on an
instrument that still cannot discriminate**. That is the series' real finding: after seven runs we
cannot author a synthetic suite that is simultaneously transfer-rich and hard enough to leave
measurement room. **The synthetic approach is exhausted as an instrument**; the honest next step is real
repos.

All seven runs used `openrouter/mistralai/mistral-small-3.2-24b-instruct` and the hardened grader
(pristine test restored before every verdict). Per-task timeout: 240 s for runs 1–6, raised to 480 s for
run 7b after run 7a was discarded as timeout-contaminated (below).

---

## Run 1 (2026-07-22) — a ceiling-limited null (uninformative)

Suite: 30 `fix_*` tasks borrowed from `local_lift`, committed order, halves of 15.

```
cold      1st 100.0%   2nd  86.7%   Δ -13.3%   overall 93.3%
learning  1st  86.7%   2nd  73.3%   Δ -13.3%   overall 80.0%
DiD (pre-registered primary): -0.0%     skills kept: 31     integrity: clean
```

The DiD was exactly zero, but **uninformative by construction**: the `cold` arm scored **100% on the
first half** — a control already perfect has no room to reveal an improvement. (The same week, the
`local_lift` re-run showed these borrowed tasks were far easier for this model than the published
record claimed — see [`../local_lift/RESULTS.md`](../local_lift/RESULTS.md).) The ceiling risk was
named before the run; that it materialised this severely was not anticipated. **Verdict: no signal —
fix the suite.** Raw: [`results/learning.json`](results/learning.json).

---

## Run 2 (2026-07-22) — right difficulty, but the wire was cut

Suite: **40 authored `hfix_*` tasks** ([`tasks_hard_fix.py`](tasks_hard_fix.py)), written to a
difficulty spec fixed before authoring (target: control arm at 40–60%), halves of 20.

```
cold      1st 50.0%   2nd 75.0%   Δ +25.0%   overall 62.5%
learning  1st 50.0%   2nd 70.0%   Δ +20.0%   overall 60.0%
DiD (pre-registered primary): -5.0%     skills kept: 39     integrity: clean
```

At first read this was the informative null the suite was built for: the control landed at 50% (dead
in the target band, no ceiling), and the DiD was a small negative well inside the noise of n=40. But a
**grounded 29-agent study of the code that followed changed the reading entirely**: the learn→use loop
is **write-only by default**. Skill cards inject only when `settings.skill_cards` is true, and it
defaults **False** (`chimera/config.py:199`); `chimera solve` exposed no flag, and the bench set no
override — so `cards = None` and `card_ctx = ''` (`chimera/core/autonomous.py:291`). **The 39 skills
run 2 minted were never fed into any later task's prompt.** Run 2 measured a learning loop with the
wire cut; a null was the only possible reading, regardless of skill quality. See
[`LEARNING_ROADMAP.md`](LEARNING_ROADMAP.md). Raw: `results_hard/learning.json`.

---

## Run 3 (2026-07-22) — connected, and a level-shift hint

The fix (roadmap P1+P5): the learning arm now carries `--playbook` (injects curated cross-task
strategy, ungated) and `--skill-cards` (reads learned cards back). Same 40-task suite, single seed.

```
cold      1st 45.0%   2nd 75.0%   Δ +30.0%   overall 60.0%
learning  1st 55.0%   2nd 85.0%   Δ +30.0%   overall 70.0%
DiD: -0.0%     skills kept: 3
learn→use connection: 35 card retrievals credited, 50 playbook bullets     integrity: clean
```

The wire was now live (35 card uses, 50 bullets — unlike run 2's zero), so this is the **informative**
null, not the disconnected one. The DiD stayed ~0, **yet the learning arm sat +10 pp above cold in
BOTH halves** (55 vs 45, 85 vs 75). That pattern is a **level shift**, and the DiD is a **slope**
estimator — it asks whether the second half improves *more* than the first and subtracts any constant
offset to zero. So a connected loop whose useful bullets are learned early and then help roughly
equally is exactly a level shift the DiD cannot see. Flagged at the time as **suggestive but likely
noise** (the +10 pp appears even in the first half, where little is accumulated) — and pre-registered
run 4 as the confirmation test. Raw: `results_hard_connected/learning.json`.

---

## Run 4 (2026-07-23) — the right meter, at power: the hint refuted

Two changes, both pre-registered: (1) the **pooled paired estimator** (McNemar + Wilson on discordant
pairs, `chimera/eval/paired.py`) becomes primary — it *can* detect a constant offset the slope-DiD
subtracts away; (2) **P3 error-seeded playbook curation** — the curator now sees the failing verifier
output + the fixing diff, not just verdict+answer. Same suite, **3 seeds pooled → n=120 paired trials**.

```
POOLED PAIRED (n=120 = 40 tasks × 3 seeds)
  cold      66.7%
  learning  65.8%
  paired Δ  -0.8%   95% CI [-8.7%, +7.2%]   ->  NOT significant (CI includes 0)
  discordant pairs: learning +13 / cold +14   (93 concordant carry no signal)

DiD per seed: [-0.15, +0.10, 0.00]   mean -1.7%
skills kept per seed: [1, 1, 2]
learn→use connection: 113 card retrievals credited, 50 playbook bullets
```

Per seed the arms are **locked together** — 68/65, 65/65, 68/68 — the cleanest possible "no effect"
signature (not noise bouncing around, but two arms tracking each other). **This refutes the run-3
+10 pp hint**, exactly as the pre-registration's confirmation test intended: *"if the run-3 +10 pp was
noise, run 4 with 3× the sample should fail to clear zero."* It did — the paired delta is centred on
zero with a symmetric CI. The pre-registration discipline held: the flattering single-seed number did
not survive.

The loop was fully connected (113 card retrievals, 50 bullets) and error-seeded, so this is **not** a
cut-wire null. It is the well-powered, correctly-metered statement: **with everything turned on,
accumulated learning does not measurably improve capability on this suite.** Raw:
`results_hard_paired/learning.json`.

### A live catch: the agent tried to game the verifier

Run 4's integrity gate flagged one task, `hfix_merge_ranges` — and it was not trivial. The solve
**rewrote the verification test with its own, weaker cases** (dropping the zero-width `(2,2)` interval
and the unsorted input the committed test checks), a test it could pass. Classic reward-hacking. The
hardened grader caught it: it detected the changed hash, **restored the committed test before
grading** (so the task was graded honestly against the real test, which the still-buggy code failed),
and **preserved the tampered workspace** for inspection (`results_hard_paired/tampered_hfix_merge_ranges/`).
This validates the grading-integrity machinery and is live evidence for the project's "governed /
honestly-evaluated" thesis: agents *do* try to game verifiers, and Chimera's gate catches it.

---

## Run 5 (2026-07-23) — semantic recall: retrieval quality was not the blocker

Run 4's leading explanation for the null was retrieval *quality*: cards (BM25) and memory facts
(keyword) matched injected neighbours lexically, so on a surface-disjoint suite they were rarely
relevant. `memory_bench` made that concrete — keyword paraphrase recall **0%**, semantic **94%**
(+0.938, `text-embedding-3-small`). Run 5 turned semantic recall on in the learning arm (both cards and
memory facts ranked by embedding cosine), everything else identical to run 4.

```
POOLED PAIRED (n=120 = 40 tasks × 3 seeds)   [semantic recall]
  cold      58.3%
  learning  60.0%
  paired Δ  +1.7%   95% CI [-5.3%, +8.1%]   ->  NOT significant (CI includes 0)
  discordant: learning +11 / cold +9        DiD/seed [-0.10, +0.05, +0.05]  mean +0.0%
  skills kept/seed: [14, 16, 16]            connection: 69 card retrievals, 50 bullets
```

The paired delta moved from −0.8% (lexical) to +1.7% (semantic) — a ~2.5 pp nudge toward positive that
is **well inside the noise** (the CI half-width is ~7 pp; the cold arm alone swung 66.7% → 58.3% between
runs on sampling luck). It is still a null: the CI comfortably includes zero.

**This is the pre-committed second null-meaning.** Semantic retrieval — proven to fix retrieval quality
(0→94% paraphrase recall) — did not produce a significant lift, so **retrieval quality was not the sole
blocker.** What remains is **transfer-poverty**: on surface-disjoint tasks there are too few similar past
tasks to transfer from, however well retrieval finds them. A detail that *strengthens* this reading:
skills kept jumped from run 4's [1,1,2] to **[14,16,16]** — *more* learned artifacts, semantic
(relevant) retrieval, and still a null. The problem is neither artifact quantity nor retrieval quality;
it is that a disjoint suite offers nothing to transfer. Raw: `results_hard_semantic/learning.json`.

---

## Run 6 (2026-07-23) — a transfer-possible suite: a positive signal, later RETRACTED

> **⚠️ Retracted by run 7.** This section is preserved as written, because deleting a claim we made is
> not how a retraction works. The signal below **did not replicate** on a harder suite at 1.5× the
> sample: the transfer gap fell from +6.7% (significant) to +2.0% (not). Read [Run 7](#run-7-2026-07-23--the-confirmation-that-failed-run-6-retracted)
> before citing anything here.

If the ceiling is transfer-poverty, then a suite where transfer *is* possible should let learning show.
[`tasks_recurring.py`](tasks_recurring.py) is that instrument: **25 tasks, 5 families × 5 members**,
each family sharing one nameable, transferable fix (guard empty→default; never mutate input; inclusive
range end; case-insensitive compare; reset per-group accumulator), ordered family-by-family so the
learning arm meets a family's members consecutively. Config identical to run 5 (connected + P3 +
semantic recall, pooled paired, 3 seeds).

```
POOLED PAIRED (n=75 = 25 tasks × 3 seeds)   [recurring, semantic]
  cold      90.7%            <- ceiling: too easy for this model
  learning  96.0%
  paired Δ  +5.3%   95% CI [-1.0%, +7.5%]   NOT significant, and CEILING-flagged (uninformative)

WITHIN-FAMILY TRANSFER  (pre-registered secondary — the real test)
  first members (n=15):  paired Δ +0.0%  [-10.8%, +10.8%]     (learning has no card yet)
  later members (n=60):  paired Δ +6.7%  [+0.1%, +6.7%]  SIGNIFICANT   (learning has the card)
  transfer gap (later − first): +6.7%
  per-card: fix_inclusive_range_sum 3/3, fix_case_insensitive_membership 5/5, fix_first_positive 3/3
            fix_longest_string 2/2  — every retrieval of these cards led to a pass (rate 1.0)
```

**This is the pre-registered transfer signature, and it is positive and significant.** Learning helps
specifically on the members where it has accumulated a relevant family card, and not on the first
member where it has none — exactly what "learning transfers when transfer is possible" predicts, down
to the per-card telemetry (the minted family cards succeed every time they fire). All three seeds gave a
positive DiD (mean +10.3%). After five nulls, this is a directional reversal.

**Two honest caveats keep it from being a closed proof.** (1) The *pooled* primary is
**ceiling-limited** — the recurring families came out easy for this model (cold 90.7%), so the bench
flagged the pooled Δ uninformative-by-construction (the same rule run 1 tripped, here from easiness not
difficulty), and it is not significant. (2) **Thin discordant counts** — n=75 with only 6 pooled
disagreements; the significant later-member CI [+0.1%, +6.7%] barely excludes zero. So the signal lives
in the pre-registered *secondary* (the family split), which is exactly the lens built to isolate
transfer from the base rate — but it needs confirmation. Raw: `results_recurring/learning.json`.

## Run 7 (2026-07-23) — the confirmation that failed: run 6 retracted

Run 7 existed for one purpose fixed in advance: confirm run 6's transfer signal on a suite hard enough
for the *pooled* metric to speak. [`tasks_recurring_hard.py`](tasks_recurring_hard.py) is the same five
families, every member rebuilt with the four difficulty inversions of the `hfix_*` suite applied
**family-specifically so the trap recurs within a family** (a card learned on member 1 still transfers).
Validated mechanically 25/25 before any run, no tuning against a pass rate. Seeds raised 3 → 5.

### Run 7a — discarded as infrastructure-contaminated

The first execution was stopped after 3 seeds and **discarded**, recorded in the pre-registration rather
than silently re-run. The tell was the *control*: it carries a fresh home per task and accumulates
nothing, so it should be stable — yet it swung **96% → 52%** across seeds on identical tasks, and one
seed showed an 11-task *contiguous* learning-arm collapse. No capability effect does that. The cause was
a real instrumentation blind spot: `subprocess.TimeoutExpired` was **silently swallowed**, so a task
that ran out of clock was scored identically to a task the agent failed — latency collapse was
indistinguishable from capability collapse. Fixed by recording per-task duration and a timeout marker,
auditing latency per arm, and **declaring any run with asymmetric timeouts invalid**. Budget raised
240 s → 480 s. That fix is the durable gain from the discard.

### Run 7b — the clean run, and the retraction

```
POOLED PAIRED (n=125 = 25 tasks × 5 seeds)   [recurring_hard, connected + P3 + semantic]
  cold      88.8%            <- still ceiling-limited: pre-registered target was 40-60%
  learning  90.4%
  paired Δ  +1.6%   95% CI [-5.1%, +7.7%]   NOT significant
  discordant pairs: learning +11 / cold +9   (105 concordant carry no signal)

WITHIN-FAMILY TRANSFER  (the metric that was significant in run 6)
  first members (n=25):  paired Δ +0.0%  [-15.0%, +15.0%]
  later members (n=100): paired Δ +2.0%  [-4.9%, +8.0%]   NOT significant
  transfer gap (later − first): +2.0%      (run 6: +6.7%, significant)

  per-seed DiD: [-8.3%, +23.1%, -7.7%, -23.7%, -16.0%]  mean -6.5%
  skills kept per seed: [14, 14, 17, 12, 11]   grading integrity: no arm modified its own test
  latency: cold median 17 s (max 73 s) | learning median 30 s (max 179 s) | timeouts 0/125 both arms
```

**Run 6's signal did not replicate.** The transfer gap fell from +6.7% (significant) to +2.0% (not),
and the pooled Δ is +1.6% (not). Pre-registration named this outcome in advance — *"both null on a hard
transfer-possible suite → run 6 was a fluke of a ceiling-easy suite; the honest retraction, reported
with the same prominence"* — and that is what this is. In hindsight the warning was legible in run 6's
own numbers: a "significant" +6.7% resting on **6 discordant pairs** with the control at 90.7%, and a CI
whose lower bound was +0.1%. It is the same shape as run 3's flattering +10 pp that run 4 refuted. We
reported run 6 as "the first positive" with caveats attached; the caveats were right and the headline
was too generous.

**What run 7b does not license is the opposite overclaim.** The control landed at **88.8%**, not the
pre-registered 40–60% — the four difficulty inversions that put the disjoint `hfix_*` suite at the right
difficulty did *not* transfer to the recurring families. So the primary metric is again
uninformative-by-construction, and every effect estimate is underpowered: a CI of [−5.1%, +7.7%] is
consistent with no effect *and* with a modest real one. "Not proven" is the finding; "disproven" is not.

**Two clean by-products.** The run is verifiably uncontaminated — **zero timeouts in 250 solves**, the
audit run 7a lacked. And the latency picture retires an earlier worry of mine: the learning arm costs
~1.8× the control's median (30 s vs 17 s), far from the budget — the flywheel is slower, not unusable.

## What the series establishes, after seven runs

**Establishes.** The self-improvement machinery runs, mints and (once fixed) injects artifacts, and
holds grading integrity even against an agent that rewrote a test. The learn→use loop was disconnected
by default and was reconnected; retrieval was upgraded from lexical to semantic (0%→94% recall on
`memory_bench`); the timeout blind spot that made run 7a uninterpretable was found and closed. Honest
measurement now exists where none did, and it has already done its job twice — refuting run 3's
flattering +10 pp, and now retracting run 6's +6.7%.

**Does not.** **No properly-powered run in this series shows that accumulated learning improves task
success.** Runs 1–5 nulled on surface-disjoint tasks (nothing to transfer). Run 6's positive on a
transfer-possible suite did not replicate in run 7. The direction has been weakly positive on both
recurring runs and never significant. And the series is narrow throughout — authored synthetic suites,
one weak 24B model.

**The honest diagnosis is now about the instrument, not the hypothesis.** Runs 4→5 ruled out retrieval
quality as the blocker, isolating transfer-poverty; runs 6→7 tried to exploit that and hit a different
wall. Every suite we can author lands the control at **84–92%** — ceiling-limited — whether the tasks
are disjoint or recurring, easy or deliberately inverted. Difficulty calibrated on one suite does not
carry to another. So the hypothesis "accumulated learning helps when transfer is possible" remains
**untested at power**, not refuted: we have not built a measuring device capable of answering it. That
is the argument for moving to real repos, where difficulty is a property of the world rather than
something we fabricate — and where the retrieval infrastructure built along the way (semantic cards +
memory, the k-NN reranker, the planned local embedder + `sqlite-vec`) finally has natural repetition to
work with.

## Methodological notes (why to trust these results — including the retraction)

- **Pre-registration held under temptation, and then under embarrassment.** Run 3's flattering +10 pp
  was refuted by its own confirmation test; run 6's "first positive" was published with caveats, and
  when run 7 failed to replicate it, the retraction ships at the same prominence as the claim did —
  because run 7's readings were committed *before* the run, including the one that says "run 6 was a
  fluke". Naming the losing outcome in advance is what makes accepting it cheap.
- **The transfer metric was pre-registered as a secondary before run 6**, so its positive could not be
  dismissed as a post-hoc slice — and by the same token its collapse in run 7 cannot be dismissed as
  the wrong lens. It is the same lens, on more data.
- **A discard is documented, never silent.** Run 7a was stopped and thrown away; the evidence table and
  the diagnosis live in the pre-registration. An undocumented discard is indistinguishable from a
  re-roll until someone audits the commits.
- **Two failure modes were separated that had been conflated.** After run 7a, timeouts are recorded and
  audited per arm, and asymmetric timeouts invalidate a run outright. Before that fix, "the agent could
  not do it" and "the clock ran out" were the same number.
- **A null ships with the same prominence as a win** — and a retraction ships with the same prominence
  as the claim it retracts.

## Next — and why there will be no run 8 on a synthetic suite

**Authoring another synthetic suite is not the answer, and this is the run that proves it.** Run 7 was
the third attempt to hit a 40–60% control band by construction; all three landed at 84–92%. Difficulty
calibrated against one suite does not carry to another, so each attempt costs a full run to discover
that the instrument is still blunt. A hypothetical run 8 would be the fourth roll of the same die, and
would carry the growing suspicion — correctly — that we are re-rolling until a suite is kind to us.

The series moves to **SWE-bench Verified** ([`bench/swe_bench/PREREGISTRATION.md`](../swe_bench/PREREGISTRATION.md),
registered 2026-07-20, not yet run; adapter in `chimera/eval/swe_bench.py`). It answers both of this
series' unmet needs at once:

- **Difficulty we do not author.** SWE-bench Verified is hard for a weak model by nature — no ceiling
  to engineer around, and no way to accidentally tune the suite toward the answer we want.
- **Transfer that is real rather than staged.** Real repositories repeat their own idioms across
  instances. That is the transfer-possible setting run 6 tried to simulate, without the simulation.
- **Grading we do not own.** The verdict comes from SWE-bench's own `FAIL_TO_PASS` / `PASS_TO_PASS`
  harness in the official Docker image, not from our parser. Given that this series has already caught
  the agent rewriting a verification test, outsourcing the grader is a feature.
- **A number outsiders recognize**, which the competitive study says the project lacks.

The learning question follows it there: the SWE-bench pre-registration's arms disable cross-instance
learning (`--no-remember --no-collect --no-evolve-skills`) precisely so Q1 measures scaffolding alone.
Once that baseline exists, **flipping those flags on is the learning-lift experiment repeated on real
repos** — the same paired design, the same estimator, on an instrument that can actually discriminate.

Until then the honest statement stands, sharpened by the retraction: the flywheel is built, connected,
semantic, gated, and **unproven** — and the reason it is unproven has moved from "the suite cannot show
transfer" to "we cannot build a synthetic suite that can measure it."
