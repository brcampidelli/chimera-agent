# Results — the check rarely declines a real task; the grounded note is the likelier harm

Run 2026-09-27 against [`PREREGISTRATION.md`](PREREGISTRATION.md), design unchanged.
**90 items (S0: 6 + 6, read by eye before S1), US$ 0.0211 measured, 0 halts.** The verifier was
`local_logprob` / `qwen3:4b@Q4_K_M` on every row, never the lexical fallback. Rows:
`results/run1/runs.jsonl`; `report.py` output: `results/run1/report.txt`.

## The registered readings

| reading | result |
|---|---|
| control gate: answerable questions declined (≤ 2 of 30) | **0 / 30**: PASS |
| **primary: `gate_declined` among 60 tasks** | **2 / 60 = 3.3%**, Wilson [0.9%, 11.4%] |
| verdict | **MODERATE**: the upper bound is 1.4 pp over the 10% line |
| per language | PT 1/30, EN 1/30 |
| per kind | critique 1/10, appraise 1/10, the other four 0/10 |
| escalated to Sol | 2 / 60 |
| the product's path: tasks the classifier reads as questions | 2 / 10 `gate_declined` |
| spend | US$ 0.0157 on tasks, US$ 0.0054 on controls |

The verifier read 56 of 60 tasks as `supported`. The median `p` was 0.999, and it included
critiques, translations into three languages and advice. The worry that motivated the run was that
a task's answer "is not supported by the excerpts". It did not hold on this verifier and this pool.

## The manual reading (§4.3)

Of the two `gate_declined`:

- **`T:en:critique:3`** ("What are the weak spots in this documentation?"). **A real attempt,
  withheld.** luna wrote a sound critique, read `unsupported` (p 0.369). Sol's redraft read
  `supported` at p 0.735, under the 0.8 threshold, so both were withheld and "The sources provided
  don't cover this." shipped. **This is the one case of the check refusing legitimate work: 1 / 60.**
- **`T:pt:appraise:3`** ("A forma descrita aqui é adequada para uso em produção? Justifique."). **Not
  a real attempt.** luna's draft was already a refusal ("O documento não informa se essa forma é
  adequada…"). Sol's redraft said the same. The check shipped a decline that says what the draft
  already said.

As registered, the primary count stays 2 / 60. By the reading it is 1 / 60.

## What the frozen routes got wrong (reported, `report.py` left unchanged)

`report.py` files every `decline_shipped` without escalation as `drafter_declined`. One of its two is
not a decline:

- **`T:en:translate:5`** ("Give me a German version of this text."). The draft is a correct German
  translation. The verifier read it as `declined` (P(supported) 0.166), so `decline_shipped` is set.
  The text shipped **unchanged**. The user gets the translation, but the receipt, and the badge
  built from it, would call it "sources don't cover this". That is a labelling defect on a good
  answer, not a withheld one.
- **`T:en:appraise:1`** ("Does this approach seem safe to me?"). luna refused ("The provided
  document does not say whether Chimera is safe…"). The verifier was right to read it as `declined`.

So "any decline shipped" (4/60 in the report) is **2 refusals in content, 1 withheld attempt and 1
mislabelled translation**.

## The finding the design did not target: refusals come from the draft

Every draft here carried `GROUNDED_NOTE`, as a turn does in the product when the classifier reads
a question. Under it, luna refused **3 of 10 appraisal tasks** on its own:

- `pt:appraise:2` ("Os documentos fornecidos não dizem se isso é uma boa ideia…"), shipped verified
  at p 0.908;
- `pt:appraise:3`;
- `en:appraise:1`.

That is more than the check declined. On the product's own path, the 10 tasks the classifier
misreads, **3 end in a refusal**: two from the draft and one from the check.

**Not registered, no control.** There is no arm without the note, so this cannot be attributed to
the note rather than to luna. A paired arm, the same 60 tasks drafted without the note, costs about
US$ 0.02 and would settle it. That is the next pre-registration.

## Predictions, scored

- **P1** primary MATTERS (≥ 25%): **wrong**. 3.3%.
- **P2** appraise and critique decline more than translate and rewrite: **right in direction**, 1+1
  against 0+0. With 2 events it says nothing.
- **P3** control 0–1 declines: **right**, 0/30.

## What follows (§5, MODERATE)

- **The classifier's Portuguese gap still gets its PR,** as registered, and becomes in-sample for
  the fresh set.
- **The decline path is left as is.** One withheld attempt in 60 tasks forced through the check,
  times a ~6% misread rate, is about 0.1% of tasks with documents.
- **Two things surfaced that the registration did not plan for,** each needing its own measurement:
  - the grounded note and appraisal refusals (paired arm, above);
  - a `declined` reading on a good answer that still ships the answer but badges it "sources don't
    cover this" (`T:en:translate:5`). The badge could say so only when the shipped text is the
    decline.

## Limits

- The documents are Chimera's own docs, the pool the verifier was measured on. The draft prompt is
  a one-line assistant prompt plus the note, not the agent's full prompt.
- With 60 tasks, 1–2 events leave wide intervals, per kind above all.
