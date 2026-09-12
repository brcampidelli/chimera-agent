# Pre-registration — a prose corpus for the fusion judge, graded without a model

Written 2026-09-12, **after** the deterministic instrument check (`corpus.py`, US$ 0) and **before**
any model call. The instrument check is the wall probe of `bench/PROTOCOL.md` §1: proof that the
planted flaw is a real, greppable contradiction of a stated fact, so a wrong pick is the judge's and
not the corpus's.

## The question the series could not ask

`bench/judge_blind` and `bench/judge_blind_qa` measured whether the fusion judge's verdict follows
the vendor name or the position rather than correctness, and both ended on the same wall, in
`bench/judge_blind_qa/RESULTS.md`:

> The series ends unless a corpus of a different kind is built: a prose turn where nothing is
> checkable, which this design cannot grade.

A model grader would reach prose but would carry the very bias under test, so it cannot be the
instrument. This corpus is the way to grade prose **deterministically**: the turn supplies a short
passage of facts and asks for a prose answer; the panel's three candidates are **authored** (so a
defect can be planted), two faithful and one that **contradicts a single stated fact** on a discrete
token — a month, a number, a name, a protocol. The grader is a token check on the synthesised final:

* `kept` — the source's `right` token is in the final and the `wrong` one is not (faithful);
* `propagated` — the `wrong` token reached the final (the flaw survived synthesis);
* `omitted` — neither token (the fact was dropped); **not scored** as pass or fail (§2r).

## Design (same shape as `judge_blind`, reused by path)

Ten items, three candidates each: `[faithful_a, faithful_b, flawed]` (flawed at index 2). Per item,
`judge_blind`'s plan: **6 named runs** (`NAMED_DESIGN` rotates the flawed candidate under each of the
three vendor names across two orders) + **3 blind runs** (two fixed orders and one shuffle). 90 runs.
The engine is `FusionEngine` with the shipped judge and synthesiser, `blind_panel` on/off; the panel
is handed to `_aggregate` directly (the answers are authored, not model-collected), so
`task_typed=False` (the default) sends every turn down the judge→synthesiser path — asserted, and a
run that is aggregated any other way is raised, never scored. Judge and synthesiser are already
bounded (`judge_max_tokens`/`synth_max_tokens`, the runaway work), so a runaway is a halt, skipped.

## Predictions (this is the open question — held loosely, written so they can be wrong)

1. **Blind propagation ≤ 0.15.** Two of three candidates are faithful and the flaw contradicts a
   fact printed in the prompt; a judge that reads should reject the lone contradictor.
2. **|named − blind| ≤ 5 pp.** Consistent with the QA series, where the vendor effect was small and
   did not replicate. This is the prediction I least trust — it is the whole reason to run.
3. **Omitted ≤ 20%.** The instruction asks for the fact, so the synthesis should state it.
4. **Vendor spread on the flawed candidate ≤ 10 pp, intervals overlapping.** No single vendor name
   makes the judge swallow the contradiction much more than another.

## Decision rule (registered before the numbers)

Read the three outcomes as they fall, and write whichever is true into `RESULTS.md`:

* **blind ≤ 0.15 and |named − blind| ≤ 5 pp** → the fusion judge is **source-faithful and not
  name-biased** on this prose dimension; the `judge_blind_qa` null extends from arithmetic to prose,
  and the series can close having asked its last question.
* **named − blind ≥ 10 pp** (more propagation when the names show, or a vendor spread beyond its
  intervals) → the bias the series suspected, now shown on **prose**; `blind_panel=True` earns a
  prose-based justification it did not have, recorded in the fusion docs.
* **blind ≥ 0.30** → an independent finding: the fusion judge **cannot reliably reject a
  prompt-contradiction in prose**, names or no names — a weakness of the judge, not of its blinding,
  and the reason a deterministic grader was needed to see it.

No code default flips on ten items; a positive on either of the last two is a reason to build the
powered version, not to change a default.

## What this cannot show (stated before it is read)

* **One dimension of prose.** It grades **faithfulness to a source the turn supplies** — chosen
  because it is the one a deterministic grader can see. Self-contradiction, non-responsiveness, tone
  and completeness still need a model and remain out of reach; this closes the checkable corner of
  the wall, not the wall.
* **Ten items is a smoke corpus** — per-family n = 1, and if blind propagation is near zero the
  corpus lacked the tension to show a name effect at all (a floor, which the report will state).
* **Authored candidates, attached names.** The vendor label is a sticker, not real provenance — which
  is exactly what a name-bias test needs, and also all it can speak to.
* **One judge and one synthesiser, one day.** No cross-model or next-day floor (PROTOCOL §5); the
  replication here is the 9 runs per item across rotation and order.

## Amendment 1 — the duo composition (dated 2026-09-12, after the trio run, before the duo run)

The trio run came back **0/90 propagated** (88 kept, 2 omitted), both arms, every vendor, every
position — every registered prediction met. But 0/90 is the floor the caveats warned about, and it
has a named cause that the result cannot separate itself from: with **two faithful candidates against
one flawed**, "the synthesiser follows the majority token" reproduces 0 propagation **without the
judge ever adjudicating the contradiction**. In that regime a vendor name has no leverage — there is
nothing to tip — so the trio can show *faithfulness under a majority* but is structurally unable to
show *name bias*. That is the same §2q/§2u shape the lessons file names: a superficial cue (the
2-to-1 token majority) is sufficient to produce the outcome, so the outcome is not evidence about the
mechanism under test.

The **duo** composition removes the majority: one faithful candidate against one flawed, so the judge
must choose, and a name or a position now has real leverage — the direct prose analogue of
`judge_blind`'s one-right-one-wrong panels. Same items, same grader, same rotation-and-order design
(6 named + 3 blind per item, 90 runs), written to `results/2026-09-12-duo.jsonl`.

**Duo predictions.** (1) Blind propagation ≤ 0.25 — higher than the trio's floor because there is no
majority cue, but still low if the judge reads the passage and sides with the faithful candidate.
(2) |named − blind| ≤ 10 pp; a larger gap, or a vendor spread beyond its intervals, is the name
effect the trio could not test. **Decision rule unchanged** — the three branches above are read on the
duo numbers, which are the ones with the tension to decide them; the trio is reported as the
faithful-under-majority check it turned out to be.
