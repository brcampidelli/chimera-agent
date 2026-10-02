# Pre-registration — how often does the verified-answers check decline a legitimate task?

Committed and published **before any paid call**. Owner-assigned (session brief, 2026-09-27, item 2).

## 1. Question

0.63.0 checks an answer grounded in attached documents with a System One verifier. When the draft
is not read as supported, it escalates to `gpt-6-sol` and then ships a decline ("the sources provided
don't cover this"). The check is meant for **questions**. A deterministic classifier
(`chimera/fusion/grounded_question.py`) routes **tasks** (summarize, critique, translate, rewrite,
judge, advise) past it, because a good task answer is not a fact in the sources and would read as
unsupported.

The classifier misreads some tasks as questions: 5/80 (6.3%, Wilson [2.7%, 13.8%]) on the fresh set,
`bench/grounded_question_classifier/RESULTS_fresh.md`. **This bench measures what happens to a task
once it reaches the check**: how often the check withholds a real attempt at the task and ships a
decline. The product's harm is roughly that rate times the misread rate.

Two features of the code make a decline likely, which is why the question is worth money:

- the verifier asks *"Is this answer supported by the excerpts?"*, which a critique or a translation
  is not, literally;
- the escalation redrafts under the **question** prompt (`DRAFT_SYSTEM`: "if the excerpts do not
  contain the answer, say that the provided excerpts do not cover it").

## 2. Items (frozen, `results/items.jsonl`)

`build_items.py` is deterministic (seed 20260927). `--check` must print `OK`.
sha256 `98969128acab7a679ece1cdd185dd868da0b051ae242251201a2f6a7b972b5ed`.

- **T, 60 tasks:** 2 languages × 6 kinds (summarize, critique, translate, rewrite, appraise,
  advise) × 5 phrasings. Each phrasing appears once, on its own excerpt from the pool the check was
  measured on (`bench/verified_cascade/results/excerpts.jsonl`, same language, 80–300 words, no
  excerpt reused). The phrasings were written by the reviewing session before any model call.
  About a third are shaped like the fresh set's misses (polite frame, advice, appraisal).
- **C, 30 controls:** answerable questions (ANS), 15 per language, sampled from
  `bench/verified_cascade/results/items.jsonl` with the excerpts each was measured with.
- Recorded at freeze time, US$ 0: the classifier reads **10 of the 60 tasks** as questions, and 28 of
  the 30 controls. Nothing was changed after seeing this.

## 3. Procedure

- **Every item is forced through the check**, whatever the classifier says. Only then does the task
  arm have n = 60 rather than the handful the classifier lets through. Each row records the
  classifier's reading, so the product's own path is also reported (descriptively, n = 10).
- **Draft:** `openrouter/openai/gpt-6-luna` (the default and the measured drafter). Its system prompt
  is a one-line assistant prompt plus the product's `GROUNDED_NOTE`, which a turn the classifier
  reads as a question carries. The user message holds the document and the message. max_tokens 4000.
- **Check:** the product's own objects, `build_grounded_answers(settings, gateway)` and
  `GroundedVerifier.verify`, with the shipped defaults: backend `local_logprob` (qwen3:4b on Ollama),
  threshold 0.8, escalation to `gpt-6-sol`, and decline text in the message's language.
  `CHIMERA_HOME` points into the output folder, so the owner's `~/.chimera` is never written.
- **Stages.**
  - **S0:** the first 6 T and 6 C. The raw drafts and shipped texts are read with the eyes (§2e of
    the method notes) before going on. S0 rows are kept and count toward S1.
  - **S1:** the rest.
- **Budget:** cap US$ 5, admission stop at US$ 4.50. Estimate: US$ 1–3, most of it Sol escalations.

## 4. Readings, fixed now (`report.py`)

1. **Control gate (§2aa).** At most 2 of 30 controls may ship a decline. `bench/verified_cascade` arm
   D declined 0 of 141 answerable items. If this fails, the apparatus is suspect and the task
   readings are **not read**: diagnose first.
2. **Primary: `gate_declined` among the 60 tasks.** The verifier read the draft as not supported,
   escalated, and a decline shipped. Wilson 95%.
   - **HARMLESS** if the upper bound is **≤ 10%**.
   - **MATTERS** if the point estimate is **≥ 25%**.
   - **MODERATE** otherwise.
3. **Manual reading.** Every task whose shipped text differs from its draft is listed verbatim. The
   reviewing session reads each `gate_declined` draft and says whether it was a real attempt at the
   task. A draft that was itself a refusal is reported as such, but it does **not** change the
   primary count, which stays the automatic one.
4. **Secondary:** the same rate per language and per kind; any decline shipped, including luna's own;
   escalation rate (cost); the rate on the 10 tasks the classifier reads as questions; total spend.
5. **Product-level estimate**, clearly labelled as a product of two measurements: fresh-set misread
   rate × primary rate.

**Predictions** (written now, to be scored):

- **P1:** the primary reads MATTERS (≥ 25%).
- **P2:** appraise and critique decline more than translate and rewrite. A translation's content is
  in the source; an opinion's is not.
- **P3:** the control passes (0–1 declines).

## 5. What follows from each verdict (the owner's rule: what the measurement recommends becomes the default, in a separate PR)

- **MATTERS:** the classifier's Portuguese gap (`RESULTS_fresh.md`) becomes the next PR. A second,
  separate change is considered: a task shape reaching the check ships the draft with an
  "unverified" badge instead of a decline. That needs its own measurement.
- **MODERATE:** the classifier fix still goes in; the decline path is left as is.
- **HARMLESS:** nothing changes; the misread rate is a cost problem only (escalations).

## 6. What this cannot show

- The agent's real turn. The draft here comes from a one-line prompt plus the grounded note, not the
  agent's full system prompt with tools. The verifier and the escalation are the product's own code.
- The quality of what shipped when Sol's redraft replaced the draft (`escalated`). A redraft under a
  question prompt may do the task worse. It is listed, not graded.
- Real traffic, real documents: the documents are Chimera's own docs, the pool the check was
  measured on.
