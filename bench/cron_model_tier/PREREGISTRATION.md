# Pre-registration — may a cron job run on the cheap tier? (P1-03 acceptance)

**Written and committed BEFORE the first paid call.** Nothing below is edited after it; changes go
in an addendum with a reason. The sha256 of this file is stamped on every journal row.

Item: `study/auditoria-2026-11/PLANO-FINAL.md`, P1-03. The code (per-job `model` / `effort` /
`max_steps` on `CronJob`) shipped in #1287. This is its acceptance: *paired, same jobs, prompt and
source data frozen, current model against a cheaper tier, 3 seeds instead of 3 days; the delivery is
checked BY SCRIPT against the frozen source; a job adopts the cheap tier only at 100% correct
numbers in every seed.*

## 1 · Question

> For each job in a fixed set, does the cheap model deliver the same numbers as the model the serve
> loop uses today — on every seed?

A per-job decision, not a model ranking. Adoption is the only outcome that changes anything: the
job then gets `model=<cheap>` in `jobs.json`.

## 2 · Arms (only the model differs)

| arm | OpenRouter slug | price in / out per M tokens |
|---|---|---|
| `current` | `deepseek/deepseek-chat-v3.1` (the install default the serve loop uses) | $0.25 / $0.95 |
| `cheap` | `deepseek/deepseek-v4-flash-0731` (the flash tier of `bench/cost_routing`) | $0.06 / $0.18 |

Prices read from the provider's `/models` at run time and recorded; cost is the provider-reported
`usage.cost` when present, else tokens × price. Temperature 0.7, `max_tokens` 1500, provider `seed`
= the cell's seed (42, 43, 44). Seeds are best-effort at the provider; the journal records every
raw answer, so a reader can see how much they varied.

## 3 · Jobs (8) — frozen, synthetic

`jobs.py`. Each job is one prompt, one frozen JSON source inlined in the message as if read from a
file, and a fixed report template (`Label: value`, one per line). The system message is the
scheduled-run note chimera sends today (`SCHEDULED_RUN_NOTE`, read from
`chimera/scheduler/surface.py`, not retyped). The gabarito is computed from the source by code.

| job | what it asks | hard part |
|---|---|---|
| `activation_weekly` | totals, activation rate, change vs previous week, best day | sums, 1-decimal % |
| `churn_report` | canceled count, MRR lost / remaining, churn % | money sums, % |
| `support_triage` | high-priority count, tickets > 48 h, oldest id and age | counting with a threshold |
| `health_alert` | unhealthy services by an OR rule, count, worst p95 over ALL services | two-condition filter |
| `health_quiet` | same rule, nothing unhealthy → the reply must be exactly `NOTHING_NEW` | the sentinel |
| `newsletter_metrics` | delivered, open rate, click-to-open, bounce rate (different denominators) | denominators |
| `reactivation_segments` | three day-buckets, pro users inactive 30+ | bucket boundaries |
| `cost_digest` | week total, mean, most expensive day, change vs previous week % | money, % |

Chosen to be the *shape* of the PassaPro / ops jobs the owner's notes name as LLM-suitable
("integers and small metrics"): activation, churn, support triage, health, newsletter,
reactivation, spend. **No trading job**: numeric trading data is a deterministic script in this
install, never an LLM job, so it is not a candidate for a cheaper model — it is a candidate for no
model.

## 4 · Scoring — by script, no judgment

A cell is **correct** iff every template field parses and matches the gabarito:
`int` exact; `dec1` within 0.05; `money` within 0.01; `str` equal case-insensitively; `set` equal
as a set of names. For `health_quiet` the cell is correct iff the whole reply is exactly
`NOTHING_NEW` (same rule as `surface.is_nothing_new`); for the other jobs `NOTHING_NEW` is wrong.
A missing label, an API error, or a truncated reply is a failure, never an exclusion.
Commentary outside the template is not scored (limit, §7). `--selftest` proves, before any
spend, that the gabarito passes itself and a corrupted one fails (the §2t invariant).

## 5 · Adoption rule (fixed now)

A job adopts the cheap tier **iff the cheap arm is correct in all 3 seeds.** One failed cell in
three means "no". There is no margin to tune. The `current` arm is reported beside it and decides
nothing, except a flag: if `current` itself is below 3/3 on a job, the job is marked "reference
imperfect" (the cheap tier may still adopt on its own 3/3; the job's prompt is then the suspect).

**Reading discipline.** 3/3 on one job is weak evidence: the Wilson 95% lower bound on a
per-run success rate is 0.44 after 3 of 3. The rule is an *operational gate* at the owner's chosen
strictness, not a proof of equivalence; RESULTS prints this next to every "YES". Eight jobs of 3
runs is 24 cells per arm; the pooled Wilson interval is reported.

## 6 · n, cost, cap

8 jobs × 2 arms × 3 seeds = **48 calls**, one completion each (~700 prompt + ~150 completion
tokens). Projected cost **under US$ 0.05**; the item's cap is **US$ 3.00**, the script stops at
US$ 2.50. A 2-cell pilot prices the run first.

## 7 · Predictions (committed before the run)

* `cheap` adopts **at least 5 of 8** jobs. The tasks are short, the data fits in context, the
  arithmetic is elementary.
* The jobs most likely to fail on `cheap` are those with a *different denominator*
  (`newsletter_metrics`) and the *money / % chain* (`cost_digest`); `health_alert`'s "worst p95 over
  ALL services" is the likeliest trap (the worst service is not always an unhealthy one).
* If `current` ≠ 100% anywhere, it will be on the same jobs.
* If both arms are 8/8 everywhere the answer is "adopt all 8 for these jobs", and the corpus is
  declared too easy to bound the cheap tier — not evidence about the other 13 production jobs.

## 8 · Limits — what this result is NOT

1. **Synthetic jobs and data.** I have no access to the production VPS. The 8 jobs are shaped like
   the production ones but are not them. The verdict holds for **these 8 jobs**, not for the 21 in
   production. Each production job needs its own pairing before `model=` is set.
2. **One completion, no tool loop.** The source is inlined; the real cron reads it through tools,
   which adds steps and failure modes (tool-call formatting, wrong file) that this does not test.
   A job whose number-reading is the risk can pass here and still fail there.
3. **Template-bound scoring.** The report is forced into labelled lines so a script can grade it.
   A free-prose report cannot be graded this way; extra numbers in commentary are not checked.
4. **Three seeds is not three days.** It samples the model's variation, not the data's: the source
   is frozen, so day-to-day shape changes (an empty day, a null field) are not exercised.
5. **No "last delivery" note** and no `effort` axis; the plan's second acceptance clause (7 days,
   repeated alerts) needs production traffic and is out of scope here.
6. Two models of one family: the cheap tier is a smaller sibling, so errors are likely correlated.

---

## Adicionado depois da rodada, em 16/11, para cumprir o PROTOCOL; não altera hipótese, métricas nem regra de adoção

Tudo acima foi commitado (`920d2210`) antes da primeira chamada paga e não foi reescrito. Esta parte
só responde, por escrito, às quatro seções de `bench/PROTOCOL.md` que o teste de convenção exige.

**§11 — intervalo.** Proporções simples (células totalmente corretas por braço): Wilson, a função
`wilson` de `chimera/eval/proportions.py` (o script chama essa função, não reimplementa a conta). Não
há bootstrap. Por job há 3 células, então o intervalo por job não é informativo; o que decide é a
regra de 3/3 da §5, que é um portão operacional, e o RESULTS diz isso.

**§12 — margem.** Não se alega equivalência nem não-inferioridade. A regra de adoção (3/3) é uma
decisão operacional por job, sem margem declarada; por isso o resultado nunca é lido como "o degrau
barato é equivalente", só como "passou no portão nestas 3 sementes".

**§13 — controles.** Braço de agente trivial: não se aplica de forma útil, porque o gabarito exige
números específicos de cada fonte (um agente que não faz nada ou devolve uma resposta fixa reprova
por construção, e o `--selftest` confirma que uma resposta corrompida reprova). Braço aleatório a
custo pareado: não se aplica, não há mecanismo de seleção. O controle que existe é o invariante do
corretor: o gabarito passa em si mesmo e uma resposta errada falha, verificado antes de qualquer
gasto. O braço `current` é referência, não controle.

**§14 — escopo de modelo.** Duas famílias não foram medidas: os dois braços são da mesma família
(DeepSeek), declarado no limite 6. O veredito recomenda *adotar* um degrau mais barato (não remover
componente), mas fica medido só em `deepseek-v4-flash-0731` contra `deepseek-chat-v3.1`; nada é
default, os campos seguem opt-in por job.
