# Pre-registration — a sufficiency gate BEFORE generation, against the verifier AFTER it

*Written 2026-10-07, before any gate call was made. Nothing in this directory has called a model when
this file is committed. Corpus, generations and labels are the frozen ones of `bench/verified_cascade`
(run 2026-09-27); the only new calls are local `qwen3:4b` reads of the gate question.*

## 1. Question

A circulating recipe ("Jev + RAG") puts a decision model **in front of** the generator: *do these
excerpts contain enough to answer? if not, answer "not in the documents" and never call the LLM.* We
already ship the opposite placement — `chimera/fusion/verified.py`, a System One verifier that reads
the **drafted answer** (`bench/verified_cascade/RESULTS.md`: wrong shipped 33 → 21 of 400, 0 of 141
answerable questions declined, local `qwen3:4b`).

> **Does a sufficiency-only gate before generation ship as few wrong answers as the shipped
> post-generation verifier, without answering fewer answerable questions, while making fewer paid
> generator calls?**

The literature says probably not on its own: "Sufficient Context" (arXiv 2411.06037) found models
still answer 35–62% of insufficient-context questions correctly, so a sufficiency label alone
over-abstains, and its best method **combines** sufficiency with generator confidence. Our own
invariant I6 (`bench/PLAN-study22-system-one.md`) records that *absence* is detected far worse than
explicit statements (0.31 vs 0.99), and "is there enough here?" is an absence question.

## 2. Apparatus (fixed)

- **Items:** the 400 frozen items of `bench/verified_cascade/results/items.jsonl` — ANS 144 (the gold
  excerpt is among the four), NCR 144 (the gold excerpt removed, related ones kept), NCP 112 (premise
  not covered), EN 194 / PT 206. Excerpts are the item's `excerpt_ids`, in its order.
- **Generations and labels:** reused, not regenerated. The drafter's answer is the recorded `d1`
  (`gpt-6-luna`), the escalation answer the recorded `f1` (`gpt-6-sol`), the post-generation reads the
  recorded `local` reads, and every label the frozen two-grader + adjudication label
  (`bench/verified_cascade/replay.py::RunData.label`). Because the gate sits **before** the generator
  and does not change its prompt, an item the gate lets through would have produced exactly the
  recorded `d1`: replaying it is the counterfactual, not an approximation of it.
- **The gate instrument** — one `Choice` through `chimera.decisions` with `LocalLogprobBackend(qwen3:4b)`
  behind a `Decider` with no calibration map, read raw, one item per call, as `verified.py` reads:
  - decision `bench.sufficiency_gate.sufficient_context`, key `sufficient_context`;
  - instructions: **"Do these excerpts contain enough information to answer the question?"**
  - options `sufficient` / `insufficient`, criteria:
    - `sufficient`: "The excerpts state everything needed to answer the question."
    - `insufficient`: "The excerpts do not state the answer to the question, or state only part of it."
  - event `sufficient` (`p_sufficient`); state `{"excerpts": [...], "question": "..."}` as JSON, the
    excerpts in the item's order, exactly as the verifier's state carries them minus the answer.
  - The wording is fixed here. No second wording is tried on these items (§2aa: a threshold read
    off one wording does not transfer to another).
- **Gate rule (primary):** the gate **passes** when the reading's choice is `sufficient` — the
  argmax of the backend's two shares, i.e. the rule that decides (§2af), not an imported 0.5 on a
  number that is not the decision. Registered secondary thresholds, diagnostic only: pass when
  `p_sufficient ≥ 0.2` and when `p_sufficient ≥ 0.8`. A full threshold sweep is printed as the
  selective curve; **no threshold chosen from it can carry the verdict**.
- **Abstention text:** when C abstains it ships the product's decline ("The sources provided don't
  cover this."). Its label is fixed by the item design, as the grader rubric fixes it: on an
  NCR/NCP item a decline is `correct`; on an ANS item it is `declined`.

## 3. Arms (all on the same items)

| arm | what ships | paid generator calls per item | local calls |
|---|---|---|---|
| **A** no gate | recorded `d1` | 1 | 0 |
| **C** sufficiency gate only | gate passes → recorded `d1`; else the decline | 1 if passed, else 0 | 1 |
| **D** shipped verifier (`D_decl`, the product variant) | `verified_cascade` replay of `D_decl`: read `d1`, ship if supported ≥ 0.8, ship the decline if `declined`, else escalate to `f1`, read, ship or decline | 1 + 1 if escalated | 1 + 1 if escalated |
| **C+D** gate then verifier | gate fails → decline; passes → D on that item | 0, or D's | 1 + D's |
| **ALL-DECLINE** (trivial, §13) | the decline on every item | 0 | 0 |
| **R** random gate (§13) | 1,000 draws abstaining on the same **number** of items as C, uniformly | as C | — |

D is replayed with `bench/verified_cascade/replay.py::outcomes` unchanged, and its `handoff` and
`decline` outcomes are both read as the shipped decline, which is what the product does. Labels for
D's shipped drafts come from the frozen run; the replay convention of the published report (a
verifier `declined` outcome is not a wrong answer) is kept so the published number is reproduced.

**Analysis set:** the items on which A, D and C all have a defined outcome (A's `d1` labelled, D's
replay defined and labelled, the gate read without a halt). The published D has 398; the count kept
and every exclusion, with its family, is printed. Nothing is excluded after reading a gate score.

## 4. Metrics

- **Wrong shipped** — items whose shipped output is labelled `wrong` (primary harm), per family.
- **Coverage** — **answerable questions answered**: ANS items on which the arm ships an answer
  labelled `correct` or `incomplete`. This is the coverage the decision rule uses: declining an
  NCR/NCP item is the right output, so counting it as lost coverage would reward answering it.
- **Selective accuracy vs coverage (diagnostic, literature sense):** coverage_all = share of the
  analysis set on which the arm ships an answer that is not a decline (an ANS item labelled
  `correct`/`incomplete`/`wrong`, or an NCR/NCP item labelled `wrong`/`incomplete`); selective
  accuracy = `correct`/`incomplete` among those. Caveat stated with it: the 12 NCR items whose fact
  survived in another excerpt (`RESULTS.md`, "Item defect") make a few NCR answers correct, and this
  diagnostic counts them as declines; the decision rule does not use it.
- **False abstention** — the gate abstains on an item whose recorded `d1` the generator **would have
  answered correctly**: ANS items with `d1` labelled `correct` or `incomplete`. Reported as a count and
  as a share of those items (Wilson 95% CI). Abstentions on NCR/NCP items whose `d1` was a correct
  decline are reported separately as **harmless abstentions** (both outputs decline).
- **Wrong prevented** — items where `d1` is `wrong` and the gate abstains.
- **Calls** — paid generator calls (luna + sol) summed over the set, and saved against A and against
  D; local calls; recorded US$ (catalogue, as the original run priced it; local = 0); local seconds.
- **Gate AUROC** of `p_sufficient` for ANS (positive) against NCR ∪ NCP, and separately against NCR
  and against NCP; 95% interval by stratified bootstrap over items (2,000 resamples, seed 26).
- **Gate behaviour** — share passing per family and language; halts; the reading's mass on the two
  labels (median, and count below 0.5).

## 5. Decision rule (fixed now)

**C wins** — "a sufficiency gate can replace the post-generation verifier" — only if, on the analysis
set, all three hold:

1. wrong shipped by C **≤** wrong shipped by D;
2. coverage (ANS answered) of C **≥** coverage of D;
3. paid generator calls of C **<** paid generator calls of D.

Lowering coverage alone is not a win: condition 2 refuses it, and the ALL-DECLINE arm (0 wrong,
0 coverage) is printed to show why. Otherwise the verdict is **C does not replace D**.

**C+D** is judged by the same three conditions against D ("adding the gate in front of the verifier
is worth it"). A, R and the secondary thresholds are context; none can change either verdict.

Paired diagnostics printed beside the rule, never in place of it: wrong(C) vs wrong(D), wrong(C) vs
wrong(A), wrong(C+D) vs wrong(D), each as exact two-sided McNemar on the discordant items with the
Newcombe paired 95% interval.

## 6. Controls

- **Replay reproduces the published numbers** before any gate number is read (§2aa): A must ship 33
  wrong of 400 and D 21 wrong of 398 (`results/run/report.txt`). If either differs, the report halts.
- **Three raw readings printed** (choice, shares, mass) before the numbers are trusted (§2e).
- **Noise floor:** the gate is re-read on the first 100 items of the order (keys `floor|…`), and flips
  of choice are counted (the verifier's local floor was 0/100). More than 2/100 flips: the report says
  the gate is not deterministic and prints the verdict with that warning.
- **Halts:** more than 5% of gate reads halting stops the verdict (not measured, never 0%).
- **Mechanism active:** C must abstain on at least one item; zero abstentions is reported as "the
  gate never acted", not as "the gate is harmless".

## 7. Predictions (written so they can be wrong)

- **P1 (literature, over-abstention):** the gate abstains on at least 5% of the ANS items the
  generator answered correctly (false abstention ≥ 5%).
- **P2 (I6, absence is hard):** gate AUROC ANS vs NCR is **≤ 0.80** and below ANS vs NCP.
- **P3:** C does **not** win: its wrong shipped is above D's 21, because the errors live on NCR
  (31 of A's 33) and NCR is the family a sufficiency question detects worst.
- **P4:** C+D ships at most D's wrong but fails condition 2 if P1 holds (a false abstention removes a
  correct ANS answer D would have shipped).
- **P5:** C saves at least 20% of paid generator calls against A (it abstains on ≥ 80 of 400).

## 8. Protocol answers (`bench/PROTOCOL.md`)

- **§11 (interval):** paired binary outcomes on ≤ 400 items → exact McNemar and Newcombe's paired
  interval (method 10), as `verified_cascade` used; Wilson for single proportions; AUROC by a
  stratified bootstrap with both classes > 100 (144 vs 256). No percentile bootstrap under N = 100.
- **§12 (margin):** **no equivalence or non-inferiority claim.** The decision rule is a point rule on
  this sample and is reported as such; "C matches D" will be written as "not more wrong answers in
  these 400 items", with the paired interval beside it, never as "as safe as".
- **§13 (controls):** trivial arm — ALL-DECLINE, run. Random arm at matched selection — R, 1,000
  draws abstaining on as many items as C, run (wrong shipped and coverage distributions, and where C
  falls in them). Grader-hijack probe — not applicable: no model in this bench writes anything a
  grader reads; every label is the frozen one. Attack-on-arguments — not applicable, no attack.
  Detection probe for a counterfactual judge — not applicable, no judge scores altered items here.
  Format-only/same-length placebo — not applicable, nothing is added to the generator's prompt.
  Rule-withdrawn arm — not applicable, no instruction-following rule is scored.
- **§14 (model scope):** gate measured on `qwen3:4b` (Q4_K_M, Ollama) only; drafter `gpt-6-luna`,
  escalation `gpt-6-sol`, verifier `qwen3:4b`. This bench recommends adding a component at most, not
  removing one; whatever it finds is stated for that model, and no default changes on it without a
  second gate family measured under this registration.

## 9. Cost and limits

US$ 0: 400 gate reads + 100 floor reads on the local GPU (≈ 0.75 s each, ≈ 7 min alone; longer when
the GPU is shared). The items are this repo's docs, written by one author family; the drafter is one
model whose errors are 94% on NCR; a gate that helps a drafter that answers when it should not
(a weaker one) is not measured here. Recorded generations mean the drafter's sampling is frozen at one
draw (`d1`); the `d2` replicate is printed as a check, not as a second verdict.
