# bench/grounded_question_classifier

Does `chimera.fusion.grounded_question.is_question` tell a **question about the attached sources**
(which the verified-answers gate checks) from a **task done with them** (summarize, critique,
translate, rewrite, judge, advise — which passes straight through, unchecked)? Deterministic, no
model call, US$ 0.

The error that matters is a task read as a question: the gate may then decline legitimate work. So
the number to read first is **precision on "question"**, and the classifier reads anything it is
unsure of as a task.

```bash
python -m bench.grounded_question_classifier.evaluate                 # messages.jsonl
python -m bench.grounded_question_classifier.evaluate --set heldout   # heldout.jsonl
python -m bench.grounded_question_classifier.evaluate --check         # exit 1 if question precision < 1
```

| file | what |
|---|---|
| `messages.jsonl` | 120 messages, 60 PT-BR / 60 EN, 30 questions and 30 tasks each, with the owner's hard cases. Written by the classifier's author **before** the classifier (sha256 `38b0379c…afa12` at writing) |
| `heldout.jsonl` | 80 messages, 40 PT-BR / 40 EN, 20/20 each, informal and hard cases. Written **after** the classifier by a separate session told not to read it (sha256 `5c2f0f5c…776c`) |
| `results/heldout_v1_before_reading_misses.txt` | the first version on the held-out set — the only out-of-sample reading |
| `results/heldout_v2.txt`, `results/messages_v2.txt` | the shipped version, after its judgement and politeness rules were widened on the held-out misses |

## Results (2026-09-27)

| set / version | class | PT precision | PT recall | EN precision | EN recall | all precision | all recall |
|---|---|---:|---:|---:|---:|---:|---:|
| held-out, v1 (**out of sample**) | question | 0.800 | 0.800 | 0.810 | 0.850 | **0.805** | 0.825 |
| held-out, v1 | task | 0.800 | 0.800 | 0.842 | 0.800 | 0.821 | 0.800 |
| held-out, v2 (after reading its misses) | question | 1.000 | 0.950 | 1.000 | 0.850 | 1.000 | 0.900 |
| held-out, v2 | task | 0.952 | 1.000 | 0.870 | 1.000 | 0.909 | 1.000 |
| messages, v2 (same author) | question | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| messages, v2 | task | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

**How to read it.** The one number that was not fitted is v1's 0.805: eight of 80 held-out tasks were
read as questions, all of them judgement or advice ("Is this a good deal?", "Quais são os riscos
desse contrato pra mim?") or a politeness frame mid-sentence ("any chance you could shorten this?").
v2 widened those two rules as classes (appraisal words, "for me / pra mim", `should`, a request verb
after "you could / você poderia" anywhere) and reads the held-out set at 1.000 — **in sample now**.
Its four remaining misses are questions read as tasks (terse noun-phrase requests like "gimme the
deadline"), the safe direction. A fresh, unseen set is what would say whether v2 generalizes.

## What this cannot show

- **The cost of a misread.** A task read as a question is only harmful if the verifier then declines
  it. The rate of false declines on real tasks needs a paid run of the gate on task messages with
  their documents — **not done**, and the next step.
- Real traffic. Both sets were written for this bench, by two sessions of one model family.
- Any language but PT-BR and EN: the classifier reads every other language as a task (unchecked).
