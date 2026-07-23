# Does accumulated learning actually help? — six pre-registered runs

Design, task order, metric and predictions were fixed in [`PREREGISTRATION.md`](PREREGISTRATION.md)
**before any model call** of each run, and each amendment was committed before the run it governs. Read
the power caveats there before reading any result below.

**Bottom line up front.** Runs 1–5 nulled on a **surface-disjoint** suite and diagnosed the cause as
**transfer-poverty** (nothing to transfer between unrelated tasks — even semantic retrieval, proven to
bridge paraphrases 0%→94% on `memory_bench`, did not move the number). Run 6 tested that diagnosis on a
**transfer-possible** suite — task families sharing one transferable fix — and produced the **first
positive signal in the series**, exactly where the theory predicts it: on the **later members of each
family** (where the learning arm has already minted the family's card) the paired lift over the
no-learning control is **+6.7%, 95% CI [+0.1%, +6.7%] — significant** — while on **first members** (no
card yet) it is **+0.0%**. That is the pre-registered transfer signature. The honest caveats: the
*pooled* primary metric is **ceiling-limited** (the recurring suite came out easy — cold 90.7% — so the
headline pooled Δ +5.3% is flagged uninformative-by-construction and is not significant), and the effect
rests on **thin discordant counts** (n=75, few disagreements). So: **suggestive, pre-registered-
secondary, significant evidence that accumulated learning helps *when transfer is possible*** — the
strongest the series has produced, and a directional reversal of five nulls — but not a closed proof.
It wants confirmation on a *harder* recurring suite (cold off the ceiling) with more seeds.

All six runs used `openrouter/mistralai/mistral-small-3.2-24b-instruct`, the hardened grader (pristine
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

## Run 6 (2026-07-23) — a transfer-possible suite: the first positive signal

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

**Establishes.** The self-improvement machinery runs, mints and (once fixed) injects artifacts, and
holds grading integrity even against an agent that rewrote a test. The learn→use loop was disconnected
by default and was reconnected; retrieval was upgraded from lexical to semantic. And the honest
measurement now exists where none did — with a clear, causally-ordered story: the connected loop shows
**no measurable lift on surface-disjoint tasks** whether retrieval is lexical (run 4) or semantic
(run 5), because there is nothing to transfer; but on a **transfer-possible** suite (run 6) the
pre-registered transfer metric goes **positive and significant** (later-member paired Δ +6.7%, and
+0.0% on first members), with per-card telemetry showing the minted family cards succeeding on every
retrieval. So the machinery *can* help — when the tasks give it something to carry.

**Does not.** Run 6 is not a closed proof: its pooled primary is ceiling-limited (cold 90.7%) and its
significant signal rests on thin discordant counts (n=75). And the whole series is narrow — authored
synthetic suites, one weak 24B model, single seed per cell within each run. It does not yet show a
lift on the *pooled* metric with room to move, nor on real repos.

**The cause was diagnosed, then the diagnosis was tested.** Runs 4→5 ruled out retrieval quality as the
blocker (semantic recall, proven to fix quality 0→94% on `memory_bench`, still nulled on disjoint
tasks), isolating **transfer-poverty**. Run 6 confirmed the flip side: give the loop a transfer-possible
suite and the transfer metric turns positive. The retrieval infrastructure built along the way
(semantic cards + memory, the k-NN reranker, the planned local embedder + `sqlite-vec`) is not wasted —
run 6 is the first place it demonstrably paid off.

## Methodological notes (why to trust these results — the nulls AND the positive)

- **Pre-registration held under temptation, both ways.** Run 3 handed us a flattering +10 pp and the
  confirmation test refuted it (we did not keep the nice number); run 6 handed us a positive, and the
  ceiling rule + thin-n caveat keep us from overselling it (we do not get to keep the whole nice number
  either). The discipline cuts in both directions.
- **The transfer metric was pre-registered as a secondary before run 6**, precisely so a positive signal
  on it could not be dismissed as a post-hoc slice. It is the lens built to isolate transfer from the
  base rate, and it is where the signal appeared.
- **A null ships with the same prominence as a win** — and so does a *qualified* win.

## Next

Run 6's signal is real but ceiling-limited on the pooled metric. Confirm it, then broaden:

1. **A HARDER recurring suite** ([`tasks_recurring_hard.py`]): the same five families, but members
   built with the four difficulty inversions of the `hfix_*` suite (contract-not-symptom, bug off the
   named line, obvious patch breaks a second case, a quiet clause) so the control lands at 40–60% —
   giving the *pooled* metric room to confirm what the family split already shows, with more seeds for
   power. This is run 7, pre-registered.
2. **SWE-bench Verified** (`chimera/eval/swe_bench.py`): the recognized real-repo number the competitive
   study says the project needs, and a naturally transfer-rich setting (real codebases repeat patterns)
   — where the built retrieval infra should pay off at scale.

Until one of those moves a properly-powered number, the honest statement stands: the flywheel is built,
connected, semantic, gated — and **unproven**, because the only suite it has been measured on cannot,
by construction, show transfer.
