# Results — the fresh set (pre-registered in `PREREGISTRATION_fresh.md`)

Run 2026-09-27 on `6d198d93` (0.63.0), classifier unchanged. Raw output: `results/fresh_v2.txt`.

## Primary: tasks read as questions

| slice | tasks read as questions | rate | Wilson 95% |
|---|---:|---:|---|
| all messages | **5 / 80** | 6.3% | [2.7%, 13.8%] |
| PT-BR | 4 / 40 | 10.0% | [4.0%, 23.1%] |
| EN | 1 / 40 | 2.5% | [0.4%, 12.9%] |
| scenarios (PT/EN pair misread on either side) | 5 / 40 | 12.5% | [5.5%, 26.1%] |

**Verdict under the pre-registered rule: inconclusive.** The point estimate (6.3%) is under 10%, but
the upper bound (13.8%) is over it. The set is too small to say the classifier generalizes, and
it does not show that it fails either.

The five, verbatim:

- `f-pt-t-04` Será que dá pra reescrever este e-mail para ficar cordial, mas sem prometer um reembolso?
- `f-pt-t-12` O que você mudaria neste currículo para uma vaga de analista de dados?
- `f-pt-t-17` A proposta de investimento parece adequada ao perfil conservador descrito no formulário? Justifique a avaliação.
- `f-pt-t-36` Me ajuda a decidir como proceder com essa cobrança? Considere as informações da carta e apresente opções.
- `f-en-t-34` Tell me whether the construction schedule seems risky and what dependencies could cause delays.

**The prediction held on type.** All five are a polite frame (`será que dá pra`), advice (`o que você
mudaria`, `me ajuda a decidir`) or appraisal (`parece adequada`, `seems risky`). These are the
classes v2 widened on the held-out set. The widening reached them in English and not in Portuguese:
the English twins of `t-04`, `t-12`, `t-17` and `t-36` were all read as tasks.

## Secondary: questions read as tasks (safe direction)

**13 / 80 = 16.3%** (Wilson [9.7%, 25.8%]), 5 PT and 8 EN. Almost all are commands or requests
(`me passa o CNPJ`, `informe a tensão`, `give me the deadline`, `find the late-payment penalty`) or
single words (`prazo?`, `deadline?`). These answers ship unchecked, the pre-0.63.0 behaviour. The
held-out set read 10% here.

## What this changes

- **Nothing in the product in this PR**, as registered. The PT gap is specific (the four PT misses
  have EN twins that pass) and a fix is a separate PR. From that PR on, this set is in-sample.
- The number that decides whether any of this matters is still unmeasured: **how many of these misread
  tasks the verifier actually declines.** A misread task is only harmful when the check then refuses
  it. That is the paid run, pre-registered separately.
