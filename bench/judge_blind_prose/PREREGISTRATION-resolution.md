# Pre-registration — the resolution number beside the bias number

**Registered 2026-09-15, before any resolution figure was computed.** Study 19, item A3
(`PLAN-study19-arxiv-sweep.md` §2 #3 and #4). **US$ 0**: a reading of the 180 stored finals in
`results/2026-09-12.jsonl` and `results/2026-09-12-duo.jsonl`.

## The claim being tested is ours

`RESULTS.md` reports the fusion judge as *"source-faithful and not name- or position-biased on this
prose — 0 / 180"*. That is a bias number with no resolution number beside it. `2609.12439` measured
the cheap way to reach 0% bias: a judge that stops discriminating and returns **ties** — under
debiasing, tie rates rose from under 1% to 31% while the bias metric improved. A 0/180 produced by a
judge that hedged every pair ("the sources disagree") or dropped the fact would be exactly as clean
on our grader's bias axis and worthless as a judge. Our register does not say which of the two we
have.

## What is already on disk, and what is not

- The **final** of every run is stored, and the grader's three classes already partition
  resolution: `kept` (the source's token, not the flawed one), `propagated` (the flawed token, with
  or without the source's), `omitted` (neither). What was never *reported* is the resolution reading
  of that partition — it was folded into "propagation rate (of scored)".
- The judge's own **analysis** was not stored for this bench (`Run` has no `judge_analysis` field;
  `judge_blind`'s runs do). So a tie the judge wrote and the synthesiser then broke is invisible here.
  This is a record defect and is fixed going forward, not read backwards.

## Metrics — fixed here

Per arm (`named`, `blind`) × composition (`trio`, `duo`), over all runs:

- **R1 — resolved to the source:** the final carries the right token (or a registered alias) and
  not the wrong one. This is `kept`, read as resolution.
- **R2 — hedged:** the final carries **both** tokens. A subset of `propagated`.
- **R3 — resolved to the flaw:** the wrong token only. The rest of `propagated`.
- **R4 — abstained:** neither token. This is `omitted`, read as resolution.
- **R5 — lexical hedge:** the final matches any of these fixed phrases, case-insensitive, whether
  or not both tokens appear: `either`, `conflicting`, `disagree`, `unclear`, `cannot determine`,
  `not certain`, `uncertain`, `some sources`, `one source`, `according to one`, `the candidates`,
  `the answers differ`, `discrepancy`, `inconsisten`. Registered so that a hedge phrased around the
  tokens is still counted; a false positive here (a faithful final that uses "either" in passing) is
  reported by reading the matched finals, not by editing the list.

**Resolution retention** = R1 rate under `blind` against R1 rate under `named`, per composition.
The **duo** composition is the known-gap pair (one faithful, one flawed, no majority to lean on)
and is the row that matters.

## Predictions

- **P1.** R1 = 180/180 after the re-grade already in `RESULTS.md` (all runs `kept`), so R2 = R3 =
  R4 = 0 by arithmetic; the reading is whether R5 finds hedges the token partition cannot see.
- **P2.** R5 ≤ 5% in every cell, with no difference between `named` and `blind` larger than the
  cell's own Wilson width.

## Decision rule

| R1 (blind, duo) | R5 (any cell) | what `RESULTS.md` says |
|---|---|---|
| = R1 (named, duo) within 5 pp, and ≥ 0.90 | ≤ 5% | the 0/180 is a **resolved** null: the judge chose the source's token in every pair and blinding cost no resolution — the number the plan asked for, and it strengthens the verdict |
| ≥ 0.90 | > 5% in some cell | resolution holds on the token, but the finals hedge in prose; the hedged finals are quoted and the 0/180 gains the caveat |
| < 0.90 or drops > 5 pp under blind | any | the 0/180 was partly bought with ties or drops; the verdict is rewritten as "bias 0 / resolution X" and the series' closing paragraph is revisited |

## What ships regardless

- `Run.judge_analysis` stored on every future run of this bench (default `""` so stored rows load).
- `report()` prints the resolution table beside the bias table, from the stored finals.
- `bench/PROTOCOL.md` §5 gains the rule: a bias number is read beside a resolution number, and a
  judge-scored comparison stores per-judge votes and reports disagreement in the separation band
  its arms fall in (`2609.12191`) — with the note that **no current bench stores per-judge votes
  over two arms**, so that half of the plan's item #4 is a rule for the next bench, not a reading
  of this one.

## What this cannot show

- A tie the judge wrote and the synthesiser broke (not stored). The reading is of the pipeline's
  output, which is what the user receives.
- Anything beyond the one dimension the corpus has (a discrete token the source states).
- Disagreement as a function of arm separation: no stored votes over arms anywhere in `bench/`.
