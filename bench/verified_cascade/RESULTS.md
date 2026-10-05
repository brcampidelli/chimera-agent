# Results — the verified cascade for grounded answers (study 26, phase 2)

**Run:** 2026-09-27, S0–S6, all gates passed, **US$ 4.89** of the US$ 20 cap. Pre-registration: `PREREGISTRATION.md` with Amendments 0–3. Raw data: `results/run/` (every call in `calls.jsonl`, the gates, the report, and the 42 adjudications).

## Addendum, 2026-10-05: the controls the default was missing (study 30, S30-33)

Registered in `PREREGISTRATION-controls.md` (commit `cef0d316`) before `controls.py` existed; US$ 0, a
replay of `results/run/calls.jsonl`. Output: `results/run/controls.json`. Read this before the
verdict below, because it changes how the verdict's headline number reads.

**The −2.8 pp alone does not show that the verifier picks the right items.** A content-blind policy
that keeps, escalates and diverts as many items as D does, placed at random, ships *fewer* wrong
answers than D: median 12 against D's 16, and 94.8% of 1,000 random placements do at least as well.
What random placement cannot do is leave answerable questions alone: it fails to answer a median of
**76** of 140 ANS items, D fails **0**. So the verifier's measured contribution is **where** it
abstains — on questions the sources do not cover — and the claim is the pair: **fewer wrong answers
and no answerable question handed off**. The default rule (§8) already required both, so the default
stands on its own rule; what changes is that "−2.8 pp" must not be quoted as if it were evidence of
verification by itself. This analysis opens nothing on the default; a separate PR decides.

| control (n = 387, see below) | D | 1,000 draws: median [min, max] | 5th pct | D below it? | predicted |
|---|---:|---|---:|---|---|
| **R1** random escalation of 16 items (the plan's kill criterion) | 16 wrong | 27 [23, 29] | 25 | **yes** (0/1,000 draws ≤ 16) | pass |
| **R2** D's shares (162 keep / 16 escalate / 209 divert) permuted — wrong shipped | 16 wrong | 12 [4, 21] | 8 | **no** (94.8% of draws ≤ 16) | fail |
| **R2** — ANS items not answered | 0 | 76 [61, 91] | 68 | **yes** | pass |

**Leave-one-category-out** (D − A paired, exact McNemar). The 0.8 threshold was fixed before the run
and nothing was fitted on the items, so this can only show whether the effect lives in one category.
- **family:** the sign holds on every leave-out. Without NCR: D 1 vs A 2 wrong on 252, p = 1.0 — the
  effect is **carried by NCR** (D 15 vs A 25 there), the "answered after the gold excerpt was removed"
  error a support verifier exists for. Without ANS: −4.5 pp, p = 0.001; without NCP: −3.6 pp, p = 0.002.
- **doc** (12 source documents): the sign holds on every leave-out, and no single document carries it
  (largest p = 0.016, without `usage`).
- **lang:** the sign holds both ways. English alone (pt left out, n = 187): 7 vs 12, 5 fixed / 0 broken,
  p = 0.0625 — the registered "carried by" reading fires, but with five discordant pairs an exact
  McNemar cannot go below 0.0625: a power limit, not a reversal. Portuguese alone: p = 0.031.

**The item set.** The controls need, on every item, what an escalation *would* ship there; 11 items lack
a labelled or read f1 draft and are left out (registered). On the remaining 387, D has 16 wrong and A
27 — the same −2.84 pp [−5.0, −1.1], 11 fixed / 0 broken, p = 0.001 as the full run. `controls.py`
checks that D's own actions, pushed through the control's machinery, reproduce the registered replay
on every item (0 mismatches) before it draws anything.

## Verdict under the frozen rule (§8, read as Amendment 3 fixes it)

| arm | wrong shipped / 400 | vs A (paired) | cost vs A | hand-offs on answerable | verdict |
|---|---:|---|---:|---:|---|
| **A** luna alone | 33 | — | 1× | 0 | baseline |
| **B** luna → Jev → Sol → hand-off | 21 | −2.8 pp, 11 fixed / 0 broken, Holm p = 0.002 | 3.74× | 0/141 | **opt-in** |
| **D** luna → local qwen3:4b → Sol → hand-off | 21 | −2.8 pp, 11 fixed / 0 broken, Holm p = 0.002 | **1.87×** | 0/141 | **opt-in and default** |
| **L** today's lexical gate | 22/389 | −0.8 pp, p = 0.61 (null) | 10.8× | 0/141 | not adopted |
| **C** Sol alone | 20/384 | −1.3 pp, p = 0.60 (null) | 19.4× | 0/140 | — |

- **B against D:** 20 against 20 wrong on 397 shared items, 4 discordant each way, p = 1.0. By the registered tie rule, **D is preferred**: it is free locally and costs half as much.
- **Decision gate against lexical gate:** B − L is −1.8 pp (raw p = 0.039, Holm 0.195), and D − L is −1.8 pp (raw p = 0.065, Holm 0.26). The direction favours the decision gate, but **not significantly after Holm**. The lexical gate matches A at 10.8× the cost, so that finding is solid.

## What the numbers say

- **Luna never shipped a wrong answer to an answerable question** (0/144). Its 33 errors are 31 on NCR (it answered when the gold excerpt was gone) and 2 on NCP. That is exactly the error a support verifier exists for.
- **The cascade never made a case worse.** Every discordant pair against A went the cascade's way.
- **Hand-offs:** under the registered primary path (luna's decline → hand-off), B hands off 210 items and D 225, almost all NCR/NCP, where declining is correct. Under the secondary variant, which ships the decline, **B hands off 38 and D 16**. The adoption rule counts only hand-offs on answerable items, and that is 0 for every arm.
- **Threshold:** at 0.5, D has 25 wrong; at 0.8 (registered), 21; at 0.9, 21. For B: 29, 21 and 17.

## The instruments

- **Verifiers on the labelled slice (S1):**
  - Jev accepts 1.2% of 686 unsupported constructions and 100% of the gold ones. It calls 37% of plain declines "unsupported", which drives unnecessary escalations.
  - The local verifier accepts 22.2% of the unsupported constructions, and 92% of "reference plus one sentence from another file".
- **On the real drafts:** AUROC is 0.908 for Jev and 0.931 for the local verifier. Both are over-confident (ECE 0.12). The lowest accepted p is 0.81 for Jev and 0.80 for the local one, the registered 0.8 threshold.
- **Graders:**
  - G1 deepseek-v4-flash passed gate G.
  - G2 mistral-small failed it (86.6% agreement, 83.0% recall on wrong) and was swapped for gemini-3.8-flash (Amendment 1), which scored 100% on the slice.
  - κ = 0.763 on 1,320 double-graded drafts.
  - 42 disagreements were adjudicated under one mechanical rule on the owner's delegation (Amendment 2). All 42 were correct.
- **Noise floors:** 0/100 flips on replayed Jev reads, 0/100 on local reads, and grader re-grade and paraphrase agreement 97.5%.

## Item defect found (reported, not repaired)

**12 NCR items** still carried the removed fact in another excerpt. There, answering was correct, and it was graded that way. NCR rates are therefore slightly conservative about luna's hallucination rate. The report should be read with that in mind; the items stay frozen.

## What this cannot show

- Grounded answers only. Tool-using turns were out of scope, and B4 found a router hurts executors.
- The items come from this repo's docs, and a single author family wrote them, so they are not a sample of real user questions.
- Luna and Sol are one vendor, so their errors may overlap.
- Jev reads uncalibrated. A calibration map fitted on these rows would be fitted on the test set and cannot be claimed from this run.
- Chat costs are catalogue-computed, not billed (Amendment 0, C.5).

## Next

Following the owner's rule (a flag the measurement recommends becomes the default, in a separate PR), the product change is:
- give the cascade's `CheapGate` a decision gate backed by the configured System One backend, with the local backend as the default verifier;
- scope it to grounded answers;
- make the third outcome (sources don't cover it) ship the decline rather than hand off, because that is the variant with 16 hand-offs.

That code does not exist yet. This run measured the policy on logged calls.

## Addendum, 2026-10-05: confident accepts per construction (study 30, S30-38)

Registered in `bench/study30_reanalyses/PREREGISTRATION.md`; full table in
`bench/study30_reanalyses/RESULTS.md` §2. On the 686 unsupported constructions, accepts at p ≥ 0.9:
**Jev 5/686, all `fabricated`**; **local 127/686**, carried by `extra` (reference plus one sentence
from another file, **89/144**) and `num` (11/29). The shipped default reads with the local verifier, so
its confident failure is specific: a right answer padded with one unsupported sentence usually passes.
