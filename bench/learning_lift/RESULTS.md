# Does accumulated learning actually help? — five pre-registered runs

Design, task order, metric and predictions were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
**before any model call** of each run, and each amendment was committed before the run it governs. Read
the power caveats there before reading any null below.

**Bottom line up front.** After five runs, the honest answer on this authored synthetic suite is a
**null with a diagnosed cause**. With the learn→use loop fully connected, error-seeded, AND upgraded
from lexical to **semantic retrieval** (a change independently proven to bridge paraphrases: keyword
recall 0% → semantic 94% on `memory_bench`), measured with a properly-powered paired estimator (n=120),
accumulated learning is still **statistically indistinguishable from no learning** (run 5 paired
Δ = +1.7%, 95% CI [−5.3%, +8.1%]; run 4 was −0.8% [−8.7%, +7.2%]). Because semantic retrieval —
the thing that fixes retrieval *quality* — did not move the number either, the ceiling is not
retrieval quality. **It is transfer-poverty: on a suite of surface-disjoint tasks there are too few
similar past tasks to transfer from, no matter how well retrieval finds them.** The machinery works;
the *suite* is the ceiling. The README claim — *"it gets better the more you use it"* — remains
**unevidenced on this suite**, and the next test must move to a suite where transfer is possible.

All five runs used `openrouter/mistralai/mistral-small-3.2-24b-instruct`, the hardened grader (pristine
test restored before every verdict), and a 240 s per-task timeout.

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

## What the series establishes — and does not

**Establishes.** The self-improvement machinery runs, mints and (once fixed) injects artifacts, and
holds grading integrity even against an agent that rewrote a test. The learn→use loop was found to be
disconnected by default and was reconnected; retrieval was then upgraded from lexical to semantic. And
the honest measurement now exists where none did: at n=120 with the right estimator, the connected +
error-seeded loop shows **no measurable lift** whether retrieval is lexical (run 4) or semantic
(run 5), and the earlier +10 pp hint was noise.

**Does not.** It does not prove learning *cannot* help. The CIs rule out a **large** effect, not a
small one — a real +3 pp would still be invisible. And the scope is narrow: one authored synthetic
suite of **surface-disjoint** tasks (each a different function, so cross-task transfer is intrinsically
limited), one weak 24B model, single seed per cell within each run.

**The cause is now diagnosed, and it moves the effort elsewhere.** Runs 4→5 were a controlled test of
the two candidate causes. Lexical retrieval (run 4) *could* have been the blocker — but semantic
retrieval (run 5), independently proven to fix retrieval quality (paraphrase recall 0→94% on
`memory_bench`), did **not** move the number either. So the ceiling is **not** retrieval quality. It is
**transfer-poverty**: a suite of surface-disjoint tasks offers too few similar past tasks to transfer
from, regardless of how well retrieval finds them or how many skills accumulate (run 5 kept 14–16 per
seed and still nulled). The next move is therefore **not** more retrieval work on this suite — it is a
suite where transfer is *possible* (recurring task families / real repos). The retrieval infrastructure
built here (semantic cards + memory, the k-NN reranker, the planned local embedder + `sqlite-vec`) is
not wasted — it pays off exactly *there*, where similar past tasks exist.

## Methodological notes (why to trust these nulls)

- **Pre-registration held under temptation.** Run 3 handed us a flattering +10 pp; the pre-registered
  confirmation test refuted it. We did not get to keep the nice number.
- **The metric was upgraded honestly.** The paired estimator was already named as a reported secondary
  in the original design; promoting it to primary in run 4 was disclosed as a structural fix (a level
  shift is invisible to a slope estimator), not a nicer-number hunt, and declared before run-4 data.
- **A null ships with the same prominence as a win** — the standing rule since run 1.

## Next

The diagnosis points at one move: **a suite where transfer is possible.** This synthetic disjoint suite
has taught what it can — it is saturated as a learning-transfer instrument. Two directions, in order:

1. **A recurring-pattern learning-lift suite** ([`tasks_recurring.py`](tasks_recurring.py)): authored
   task *families* where members share a solution approach, so solving one genuinely helps the next.
   This is the clean controlled closer — *when transfer is possible, does the (now semantic) loop
   help?* — and it isolates the effect the disjoint suite structurally could not.
2. **SWE-bench Verified** (`chimera/eval/swe_bench.py`): the recognized real-repo number the competitive
   study says the project needs, and a naturally transfer-rich setting (real codebases repeat patterns).

Until one of those moves a properly-powered number, the honest statement stands: the flywheel is built,
connected, semantic, gated — and **unproven**, because the only suite it has been measured on cannot,
by construction, show transfer.
