# Results — the verified cascade for grounded answers (study 26, phase 2)

**Run:** 2026-09-27, S0–S6, all gates passed, **US$ 4.89** of the US$ 20 cap. Pre-registration: `PREREGISTRATION.md` with Amendments 0–3. Raw data: `results/run/` (every call in `calls.jsonl`, the gates, the report, and the 42 adjudications).

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
