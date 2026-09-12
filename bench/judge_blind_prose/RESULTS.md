# Results — the fusion judge kept faith with the source in every prose run; the bias regime is where a deterministic grader cannot follow

Run 2026-09-12 against [`PREREGISTRATION.md`](PREREGISTRATION.md) (and its Amendment 1, added after
the trio run and before the duo run). Judge and synthesiser are the shipped defaults. Raw rows:
[`results/2026-09-12.jsonl`](results/2026-09-12.jsonl) (trio),
[`results/2026-09-12-duo.jsonl`](results/2026-09-12-duo.jsonl) (duo). Reproduce:

    python bench/judge_blind_prose/run.py --run                     # trio: 2 faithful + 1 flawed
    python bench/judge_blind_prose/run.py --run --composition duo   # duo:  1 faithful + 1 flawed

## Verdict: the fusion judge is source-faithful and not name- or position-biased on this prose — 0 / 180

Across **180 runs** — both compositions, both arms, every vendor label, every position — the
synthesised final carried the flawed candidate's contradicting token **zero** times.

| composition | arm | runs | propagated | kept | propagation rate | Wilson 95% | judge names a vendor |
|---|---|---:|---:|---:|---:|---|---:|
| trio (2 faithful + 1 flawed) | `named` | 60 | 0 | 60 | **0.00** | [0.00, 0.06] | 0/60 |
| trio | `blind` | 30 | 0 | 30 | **0.00** | [0.00, 0.11] | 0/30 |
| duo (1 faithful + 1 flawed) | `named` | 60 | 0 | 60 | **0.00** | [0.00, 0.06] | 0/60 |
| duo | `blind` | 30 | 0 | 30 | **0.00** | [0.00, 0.11] | 0/30 |

Per-item, per-vendor and per-position tables are all zero (20 named runs per vendor in each
composition; positions balanced). The primary paired comparison, named → blind, is 0.00 → 0.00 in
both. Total cost **US$ 0.69**. Every registered prediction held.

**Against the registered decision rule**, this is the first branch: *blind ≤ 0.15 and |named − blind|
≤ 5 pp → the fusion judge is source-faithful and not name-biased on this prose dimension; the
`judge_blind_qa` null extends from arithmetic to prose.*

## The duo mattered, and the grader had a blind spot the run exposed

Two things this write-up would be dishonest without.

**The trio alone could not have shown a bias.** With two faithful candidates against one flawed, a
synthesiser that merely follows the majority token reaches 0 propagation without the judge ever
adjudicating the contradiction — so a vendor name has nothing to tip (PROTOCOL §2q/§2u: a
superficial cue, here the 2-to-1 majority, is sufficient to produce the outcome). The **duo**
composition (1 faithful vs 1 flawed, no majority) removes that cue: the judge must choose, and a name
or a position now has leverage. It came back 0 / 90 as well. **The null survives the removal of the
confound** — which is the only reason it is reported as a null about the judge rather than about the
majority.

**Six "omitted" were a grader miss, not the judge's.** The first pass scored six `datastore` runs
`omitted`; reading the finals, the judge had written **"PostgreSQL"** where the source said
"Postgres", and `\bPostgres\b` does not match inside `PostgreSQL` (§2l/§2t: a token grader's silent
blind spot — the instrument check tested the authored strings, not the synthesiser's paraphrase). The
fix is `Item.right_aliases` (`PostgreSQL` for that item) and a `report` that **re-grades from the
stored `final`** rather than trusting the `verdict` written at run time (§2z), so the correction
reaches every past run with no re-run. Re-graded, all 180 scored runs are `kept`. The propagation
result never depended on it — the wrong token (`Redis`) appeared in no final either way.

## What this answers, and the wall it makes precise

This is the question `bench/judge_blind_qa` said it could not ask — *a prose turn where nothing is
checkable, which this design cannot grade.* It can now be graded, one way: plant a contradiction of a
**fact the turn itself supplies** and grep the synthesis for the discrete token. On that dimension —
**faithfulness to a supplied source** — the fusion judge is clean, and blinding changes nothing
because there was nothing to bias.

And that last clause is the real finding, larger than the null. **The deterministic grader and the
bias regime are mutually exclusive by construction.** To grade prose without a model, the defect must
be checkable; a checkable defect is one the judge can also check; so wherever the grader can score,
the judge already has the answer and has no reason to lean on a vendor's name. The bias the series
feared (arXiv 2609.08016) lives where the judge is *uncertain* — tone, framing, completeness, which
of two plausible readings is better — which is exactly where nothing is checkable and **no
deterministic grader can follow.** That is not a gap in this corpus; it is why the SimpleQA series
stopped, and this bench shows the stopping point was structural rather than a corpus nobody had built.
A model grader would reach that regime and would carry the very bias under test, so it cannot be the
instrument. **The series closes here.**

## What this cannot show (as registered)

- **One dimension of prose** — faithfulness to a supplied source, the one a deterministic grader can
  see. Self-contradiction, non-responsiveness, tone, completeness remain out of reach.
- **A floor, and now known to be structural.** 0/180 is not headroom the judge happened not to use;
  it is the regime where the fact is checkable, which is the only regime this instrument can enter.
- **Authored candidates, attached names; one judge, one synthesiser, one day.** No cross-model or
  next-day floor (PROTOCOL §5); the replication is the 9 runs per item across rotation and order,
  ×2 compositions.
