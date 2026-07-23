# Does accumulated learning actually help? — four pre-registered runs

Design, task order, metric and predictions were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
**before any model call** of each run, and each amendment was committed before the run it governs. Read
the power caveats there before reading any null below.

**Bottom line up front.** After four runs, the honest answer on this authored synthetic suite is:
with the learn→use loop **fully connected** and **error-seeded**, measured with a **properly-powered
paired estimator (n=120)**, accumulated learning is **statistically indistinguishable from no
learning** (paired Δ = −0.8%, 95% CI [−8.7%, +7.2%]). The machinery is real and demonstrably wired
(113 skill-card retrievals, a 50-bullet playbook); its benefit is simply not measurable here. The
project's README claim — *"it gets better the more you use it"* — remains **unevidenced on this suite**.

All four runs used `openrouter/mistralai/mistral-small-3.2-24b-instruct`, the hardened grader (pristine
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

## What the series establishes — and does not

**Establishes.** The self-improvement machinery runs, mints and (once fixed) injects artifacts, and
holds grading integrity even against an agent that rewrote a test. The learn→use loop was found to be
disconnected by default and was reconnected. And the honest measurement now exists where none did: on
this suite, at n=120 with the right estimator, the connected + error-seeded learning loop shows **no
measurable lift** over a no-learning control, and the earlier +10 pp hint was noise.

**Does not.** It does not prove learning *cannot* help. The CI [−8.7%, +7.2%] rules out a **large**
effect, not a small one — a real +3 pp would still be invisible. And the scope is narrow: one authored
synthetic suite of **surface-disjoint** tasks (each a different function, so cross-task transfer is
intrinsically limited), one weak 24B model, single seed per cell within each run.

**The most likely mechanism, and it is actionable.** Card and memory retrieval are **lexical**
(keyword / BM25; semantic recall off by default) — so the "neighbours" the loop injects are matched on
surface tokens, not meaning, and on a disjoint-domain suite they are rarely genuinely relevant. That
is a strong candidate for *why* nothing transfers, and it points at a concrete next move: **embedded
semantic memory (`sqlite-vec`)** so retrieval returns relevant neighbours, which would also sharpen
the new inference-time k-NN reranker.

## Methodological notes (why to trust these nulls)

- **Pre-registration held under temptation.** Run 3 handed us a flattering +10 pp; the pre-registered
  confirmation test refuted it. We did not get to keep the nice number.
- **The metric was upgraded honestly.** The paired estimator was already named as a reported secondary
  in the original design; promoting it to primary in run 4 was disclosed as a structural fix (a level
  shift is invisible to a slope estimator), not a nicer-number hunt, and declared before run-4 data.
- **A null ships with the same prominence as a win** — the standing rule since run 1.

## Next

Not more of the same loop. The leads the series points to: **semantic retrieval (`sqlite-vec`)** so
injected neighbours are relevant; the **inference-time success reranker** (changes candidate
*selection*, not just injected context, which did not move the needle here); and a **less synthetic,
transfer-richer suite** — toward the SWE-bench Verified number the project needs to be taken seriously.
Until one of those moves a properly-powered number, the honest statement stands: the flywheel is built,
connected, gated, and **unproven** on this suite.
