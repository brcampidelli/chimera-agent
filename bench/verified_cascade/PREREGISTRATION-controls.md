# Pre-registration — two controls for the verified-cascade default (study 30, S30-33)

**Written 2026-10-05, committed before `controls.py` exists and before any number below the
published report is computed.** US$ 0: a replay of `results/run/calls.jsonl`, no model call.

## Why

Arm D (luna → local qwen3:4b reads the draft → Sol → hand-off) became the shipped default on
**21 wrong shipped against A's 33 (−2.8 pp paired, 11 fixed / 0 broken, Holm p = 0.002)**
(`RESULTS.md`). Two controls the literature names are absent:

1. **A content-blind control at the same share.** Router gains can track *how much* traffic goes
   to the strong tier rather than *which* items go there (arXiv 2608.14641). The question: does
   the local verifier's choice of items matter, or would random routing at D's shares do as well?
2. **Leave-one-category-out.** Routing gains can vanish on held-out categories (arXiv 2610.01535,
   "False Floors"). D's threshold (0.8) was fixed before the run and nothing was fitted on the items,
   so there is no fitted parameter to leak. Leaving a category out can therefore only show whether
   the effect is **carried by one category** — not whether a fit overfits. That limit is stated now.

## What is already public (and therefore not a blind reading)

From `results/run/report.txt`, already read: D 21 wrong / 398, 16 escalated, 225 hand-offs (16 in
D_decl); A 33 / 400 (32 on the 398 shared); wrong by family A ANS 0/144, NCP 2/112, NCR 31/144 and
D ANS 0/144, NCP 1/112, NCR 20/142. So the leave-one-*family*-out signs below are essentially
decidable from published counts; the per-document and per-language splits and both controls are not.

## The item set

`P` = the items where A and D both have a label, **and** the escalation outcome (below) is defined.
`n(P)` is printed. D and A are recomputed on `P`.

D's action on an item is one of:
- **keep** — the local read of d1 accepts (supported, p ≥ 0.8): ship d1;
- **escalate** — the read is neither an accept nor `declined`: read f1 with the local verifier;
  ship f1 if accepted, else hand off;
- **divert** — the read is `declined`: hand off (D) or ship the decline (D_decl). Neither is wrong.

The **escalation outcome** of an item is what *escalate* would ship there: `label(f1)` if the local
read of f1 accepts, else hand-off. It is defined when the f1 read exists and, if it accepts, f1 has a
label.

## Control R1 — tier-share-matched random escalation (the plan's control; the kill criterion)

Per draw: choose uniformly, without replacement, `k` items of `P`, `k` = D's number of escalated
items on `P`. Those get their escalation outcome; every other item ships d1. Count wrong shipped.
1,000 draws, `random.Random(3033)`.

**Reading.** D **passes** R1 if its wrong count on `P` is strictly below the 5th percentile of the
1,000 draws (`stats.percentile(q=0.05)`). Also printed: the fraction of draws with wrong ≤ D's.

**Prediction.** Passes. Escalating ~16 random items of ~398 removes about 16 × 32/398 ≈ 1.3 wrong
answers in expectation, so draws should sit near 30, far above 21.

**If D fails R1**, the RESULTS addendum says so with the same prominence as the original claim, and
the default is reconsidered in a separate PR. This analysis opens nothing on the default.

## Control R2 — all-shares-matched random routing (stricter; reported with equal prominence)

R1 matches only the share sent to Sol. D also *diverts* most items (no answer shipped), and a
diversion can never be wrong, so R1 is lenient. R2 permutes D's whole action vector over `P`
(uniform random permutation; same counts of keep / escalate / divert), applies each action to the
item it lands on, and counts per draw:
- **wrong shipped**, and
- **ANS items not answered** — an ANS item diverted, or escalated and handed off.

1,000 draws, `random.Random(3034)`.

**Readings.**
- R2-wrong: D below the 5th percentile of the draws' wrong count (same rule as R1).
- R2-ANS: D's ANS-not-answered count below the 5th percentile of the draws'.

**Prediction.** R2-wrong **fails**: diverting ~60% of items at random removes ~60% of A's wrong
answers, about 32 × 157/398 ≈ 12.6 plus a few from f1 — below 21. R2-ANS **passes** by a wide margin
(D: 0; random: roughly 0.6 × 141 ≈ 85).

**Pre-stated interpretation.** If R2-wrong fails and R2-ANS passes, then a fewer-wrong count by
itself is *not* evidence of verifier skill — content-blind abstention at D's share ships fewer wrong
answers still — and what the verifier measurably contributes is **where** it abstains: on unanswerable
questions, not on answerable ones. The addendum will then restate D's claim as the pair (fewer wrong
**and** zero answerable hand-offs) and say plainly that the −2.8 pp alone does not distinguish D from
random abstention. That is a correction of how the claim reads, not of the default rule, which already
required both (§8: fewer wrong **and** hand-offs on answerable ≤ 5%). The default's fate is not
decided here.

## Leave-one-category-out

For each category `g` of a split, on `P`: D − A paired on `P \ g` and on `g` alone — wrong counts,
Newcombe difference and 95% CI, exact McNemar. Splits:
- **primary — `family`** (ANS / NCR / NCP), the field the items carry under that name;
- secondary — `doc` (source document) and `lang` (en / pt).

**Readings.**
- **Sign:** the plan's correction criterion — D − A ≥ 0 on any `P \ g` ("loses its sign").
- **Carried by one category:** leaving `g` out makes the exact McNemar p ≥ 0.05. Reported as a scope
  statement (the effect lives in `g`), not as a correction of sign.

**Prediction.** The sign holds on every leave-out of every split. Leaving NCR out leaves about one
discordant pair (published: NCP 1 vs 2, ANS 0 vs 0), so the effect is **carried by NCR** — expected,
because NCR is exactly the error a support verifier exists for.

## Outputs

`controls.py` prints the readings and writes `results/run/controls.json`. `RESULTS.md` gets an
addendum with whatever comes out, at the top, next to the verdict it qualifies.
