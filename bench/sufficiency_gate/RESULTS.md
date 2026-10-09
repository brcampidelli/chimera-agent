# Results — a sufficiency gate before generation, against the verifier after it

*Run 2026-10-07 from the coordinator's serial GPU queue. Pre-registration: `PREREGISTRATION.md`
(`3ac95521`, committed before any gate call). 400 gate reads + 100 floor re-reads on local
`qwen3:4b` (Q4_K_M, Ollama), 0 halts, median 0.80 s a read, 6.9 min of model time, US$ 0. Rows:
`results/run/calls.jsonl`; report: `results/run/report.txt` / `report.json`. Generations, verifier
reads and labels are the frozen ones of `bench/verified_cascade/results/run/` (2026-09-27).*

## The verdict in one line

**C does not replace D, and C+D is not worth adding.** Under the registered rule (argmax), the
sufficiency gate ships **fewer** wrong answers than the shipped verifier (15 against 21) and makes
**fewer than half** the paid generator calls (184 against 414) — but it answers **9 fewer
answerable questions** (135/144 against 144/144). Condition 2 (coverage ≥ D) fails, and the rule
was written so that a win bought with coverage is not a win. The same holds for gate-then-verifier
(14 wrong, 135/144).

## 1. Primary arm — the argmax rule (the only one that decides)

Analysis set: 398 items (2 NCR excluded because D's replay is undefined there, as in the published
report; none excluded after a gate score).

| arm | wrong shipped | ANS answered (coverage) | abstained | paid gen. calls | local calls | US$ (catalogue) | wrong by family |
|---|---:|---:|---:|---:|---:|---:|---|
| **A** no gate | 32 | 144/144 | 0 | 398 | 0 | 0.0634 | NCR 30, NCP 2 |
| **D** shipped verifier (`D_decl`) | **21** | **144/144** | 225 | 414 | 414 | 0.1184 | NCR 20, NCP 1 |
| **C** sufficiency gate | **15** | **135/144** | 214 | **184** | 398 | 0.0297 | NCR 15 |
| **C+D** gate, then verifier | 14 | 135/144 | 242 | 187 | 585 | 0.0440 | NCR 14 |
| ALL-DECLINE (trivial) | 0 | 0/144 | 398 | 0 | 0 | 0 | — |

| §5 condition | C | C+D |
|---|---|---|
| 1. wrong ≤ D (21) | ✔ 15 | ✔ 14 |
| 2. coverage ≥ D (144) | **✘ 135** | **✘ 135** |
| 3. fewer paid calls than D (414) | ✔ 184 | ✔ 187 |
| **verdict** | **does not replace D** | **not worth adding** |

- **False abstention: 9/144 = 6.3%, Wilson 95% [3.3%, 11.5%]** — ANS questions the drafter answered
  correctly in the recorded run, blocked before it was asked. Their `p_sufficient` ranged 0.01–0.42;
  7 of the 9 are English, spread over five documents.
- Harmless abstentions (NCR/NCP items whose recorded draft was a correct decline): 188. Wrong answers
  prevented: 17 of A's 32.
- Paired, diagnostic only: C vs D 15 vs 21, 7 fixed / 1 broken, McNemar p = 0.070, diff −1.5 pp
  [−3.3, +0.1]; C vs A 15 vs 32, 17 / 0, p < 0.0001; C+D vs D 14 vs 21, 7 / 0, p = 0.016, diff
  −1.8 pp [−3.5, −0.3]. These show the gate does catch errors the verifier lets through; none of
  them can override condition 2.
- Literature-sense selective view (diagnostic, with the §4 caveat on the 12 defective NCR items):
  coverage_all / selective accuracy A 0.442 / 0.818, D 0.415 / 0.873, C 0.377 / 0.900, C+D 0.374 / 0.906.

## 2. Controls

- **Replay reproduces the published numbers** before any gate number was read: A **33/400** and D
  **21/398** wrong, exactly as `bench/verified_cascade/results/run/report.txt`.
- **Random gate at the same selection (R, 1,000 draws abstaining on 214 items):** wrong shipped median
  15 [6, 26] — 61.3% of draws ship ≤ 15 wrong, so the gate's wrong count by itself is no better than
  abstaining at random on as many items. What random placement cannot do is keep the answerable
  questions: median 66 of 144 answered [49, 85], and **0/1,000 draws reach the gate's 135**. The
  gate's measured skill is *where* it abstains — the same shape `verified_cascade`'s controls found
  for the verifier.
- **All-decline:** 0 wrong, 0 coverage — why coverage is in the rule.
- **Raw readings** (three, printed before the numbers): ANS `sufficient` 0.9995; NCR `insufficient`
  0.998; NCR `sufficient` 0.851 (a miss, kept). Label mass median 0.9996, none below 0.5.
- **Floor:** 0/100 choice flips on re-reads, max |Δp| 0.034. **Halts:** 0/400. **Mechanism active:**
  214 abstentions.
- **Replicate draft (`d2`):** C2 16 wrong against A2 33 — the same picture on the second recorded draw.

## 3. Predictions against the outcome

| | prediction | outcome |
|---|---|---|
| P1 | false abstention ≥ 5% (literature: sufficiency alone over-abstains) | **confirmed on the point, not beyond doubt**: 6.3%, Wilson [3.3, 11.5] — the lower bound is under 5% |
| **P2** | **gate AUROC ANS vs NCR ≤ 0.80**, and below ANS vs NCP | **REFUTED.** ANS vs NCR **0.944** [0.918, 0.966]; ANS vs all NC **0.958** [0.938, 0.975]. The ordering part holds in direction (NCP 0.976 [0.960, 0.989]) but the intervals touch. I expected the absence question to be weak; on these items it is strong. |
| P3 | C does not win, **because** its wrong shipped is above D's 21 | **verdict confirmed, mechanism refuted**: C does not win, but on coverage — its wrong count (15) is *below* D's |
| P4 | C+D wrong ≤ D, fails condition 2 | **confirmed** (14 ≤ 21; 135 < 144) |
| P5 | C saves ≥ 20% of paid calls against A | **confirmed**: 184 vs 398, −54% |

## 4. Diagnostics — secondary thresholds and the sweep (cannot carry the verdict)

| gate rule | wrong | coverage | false abstention (Wilson) | paid calls | C vs D paired |
|---|---:|---:|---|---:|---|
| p ≥ 0.2 | 19 | 141/144 | 3/144, 2.1% [0.7, 5.9] | 229 | 5 / 3, p = 0.73 |
| **argmax (primary)** | **15** | **135/144** | **9/144, 6.3% [3.3, 11.5]** | **184** | 7 / 1, p = 0.070 |
| p ≥ 0.8 | 9 | 126/144 | 18/144, 12.5% [8.1, 18.9] | 150 | 12 / 0, p = 0.0005 |

The sweep is the trade-off one would expect: wrong falls from 32 to 4 as the threshold rises from 0
to 0.95, and coverage falls from 144 to 112. **No threshold in the sweep meets all three conditions**:
the only one at 144/144 is t = 0, which is no gate (32 wrong); 141/144 at t = 0.15–0.20 ships 19–21
wrong. Even reading thresholds after the fact — which the registration forbids for the verdict — the
gate cannot match D's coverage without giving back D's wrong count. The p ≥ 0.8 row is the
tempting one (9 wrong, significantly fewer than D) and it pays for it with 18 answerable questions.

## 5. How this sits with S30-62 and with I6

- **S30-62** (`bench/rag_rerank/RESULTS-cross-encoder.md`, same day): a retrieval-trained
  cross-encoder in front of generation ties the fusion it re-orders. Different subsystem, same
  lesson at a small scale: a model-in-front step has to beat what the pipeline already does, and
  here neither did by the registered rule. Nothing more general than that follows from two benches.
- **I6** (`bench/PLAN-study22-system-one.md`: absence detected at 0.31 against 0.99 for explicit
  statements — "never ask 'is anything missing?'"). **This measurement does not refute I6, and P2's
  refutation is mine, not I6's.** The two ask different questions. I6's absence is an open search in
  a state for something unnamed being missing; the gate asks whether four short excerpts contain
  the answer to **one named question** — a targeted lookup against an explicit target, much closer
  to the "explicit statement" side of I6. Where I6's shape *does* show is the tail: the gate let
  through 35 of 142 NCR items (the gold excerpt removed, its neighbours kept), and **15 of those 35
  are drafts the drafter got wrong — 43%, against 21% (30/142) over all NCR.** The gate's misses
  fall exactly where the excerpts look like they answer, which is also where the drafter is fooled:
  correlated failure. The verifier behind the gate catches only **1** of the 15 errors the gate lets
  through (C 15 → C+D 14): the items that fool the gate mostly fool the post-generation reader too.

## 6. What this cannot show

- **One gate model** (`qwen3:4b`, Q4_K_M), **one wording**, **one corpus** (this repo's docs, one
  author family wrote the questions), **one drafter** (`gpt-6-luna`, whose errors are 94% on NCR).
  A drafter that answers more when it should not would make the gate's catches worth more and its
  false abstentions cost the same; that is not measured.
- **Replay of recorded generations**: exact for the gate-before-generator counterfactual (the gate
  does not touch the prompt), but the drafter's sampling is frozen at the recorded draws (`d1`,
  replicated on `d2`). No new generation was made.
- Latency is not compared: the gate adds a local read on every item, the verifier on every drafted
  item; on this machine both are ≈ 0.8 s.
- §12: no equivalence or non-inferiority claim. "C ships fewer wrong answers than D" is a point
  reading on 398 items with p = 0.070, not "safer".
- §14: measured on one gate family; nothing here changes a default.
