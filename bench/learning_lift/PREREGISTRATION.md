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
