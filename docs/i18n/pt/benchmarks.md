---
source_sha256: c43eb27971827466c65af13024113757f691c30d3666c4aa73c60105c08c56ab
---

# Benchmarks — provando o ganho no modelo fraco

A tese do Chimera é que a estrutura faz um modelo **fraco/barato** render acima do seu peso. A
forma honesta de mostrar isso é um A/B controlado em um benchmark padrão: fixar o subconjunto de
tarefas e o modelo, deixar como **única** variável o scaffolding, e reportar o delta com um
intervalo de confiança — não um simples "melhorou". (Pesquisa independente encontra o mesmo modelo
oscilando ~7pts só por causa do scaffolding, então um score sem qualificação não diz nada sobre
*a sua* contribuição.)

## O experimento

**Benchmark:** [Terminal-Bench 2.0](https://www.tbench.ai/) — tarefa Docker + instrução +
testes de verificação, avaliados pass/fail por esses testes, conduzidos pelo harness
agnóstico-de-agente **Harbor**.

- **Braço A (baseline):** um modelo gratuito no scaffold neutro do Harbor — "modelo fraco
  sozinho".
- **Braço B (tratamento):** o **mesmo** modelo, os **mesmos** IDs de tarefa, conduzido pelo
  Chimera.
- **Métrica:** pass@1. **Manchete:** Δ = taxa(B) − taxa(A), com IC de 95%.
- **Salvaguardas de honestidade:** fixar o subconjunto de IDs de tarefa (publicá-lo), rodar ≥3
  seeds, publicar todos os transcripts, e incluir uma linha de modelo de fronteira só como
  *referência de teto* — nunca como a comparação.

O único número que prova a tese: **modelo gratuito sozinho = X%, modelo gratuito + Chimera = Y%,
mesmas tarefas, Y ≫ X.**

## Como executar

```bash
uv sync --extra bench            # installs terminal-bench (Harbor); also needs Docker
playwright install chromium      # only if a task needs the browser tool
```

O Chimera se encaixa como o agente de tratamento via `chimera/eval/terminal_bench.py`
(`make_chimera_tb_agent(model)` monta um `BaseAgent` do Harbor que roda `chimera solve` com as
flags de scaffolding). Aponte o Harbor para um subconjunto fixo e um modelo gratuito para cada
braço; veja os [docs do Harbor](https://www.tbench.ai/) para a invocação exata de `harbor run` e
`--agent-import-path`.

## SWE-bench Verified (o segundo placar) — **executado, duas vezes**

O Terminal-Bench prova a tese em tarefas de CLI; o SWE-bench prova em correções de bug reais do
GitHub — dado um repositório em um commit-base e uma issue, o agente precisa produzir um patch que
faça os testes `FAIL_TO_PASS` da instância passarem mantendo os `PASS_TO_PASS` verdes.
"Verified" é o subconjunto validado por humanos.

### Resultados

Duas execuções pré-registradas na mesma fatia congelada de 19 instâncias de `django/django`
(estrato de dificuldade mais fácil), `deepseek-chat-v3.1`, pass@1, avaliadas **apenas** pelo
harness oficial `swebench` 4.1.0 em Docker. Relato completo:
[`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md).

| execução | baseline | + Chimera | Δ pareado | IC 95% | |
|---|---|---|---|---|---|
| 1 (`max_steps=8`) | 36.8% (7/19) | 36.8% (7/19) | +0.0% | [−8.5%, +8.5%] | não significativo |
| 2 (`max_steps=30`) | 42.1% (8/19) | **57.9% (11/19)** | **+15.8%** | [−1.9%, +15.8%] | não significativo |

A execução 1 é um **zero exato** e é publicada sem alteração. A execução 2 corrigiu duas falhas
que eram **nossas** — o scaffold rodou sem seu mecanismo mais forte, e 8 passos de tool-calling não
são suficientes para navegar um repositório de 250 MB — e saiu com **3 instâncias vencidas, 0
perdidas**. O par é o achado: o scaffold vale *nada* quando o agente é privado de passos e *três
instâncias* quando não é, e ele vence editando **melhor** (69% vs. 57% de precisão quando edita),
não editando mais.

> ⚠️ **57,9% não é um score de SWE-bench Verified.** A fatia é deliberadamente fácil e de
> repositório único, escolhida para que um A/B pareado tenha margem de medição; um score Verified
> de verdade precisa dos 500 completos. E o delta **não é significativo** — com 8 pares onde ambos
> falham, n=19 deixa só três pares informativos.

A execução 2 também traz uma **retratação**: o mecanismo que havíamos rastreado para os patches
vazios da execução 1 estava errado (o problema era o orçamento de passos, não o diff-gate que
havíamos culpado), corrigido com o mesmo destaque com que foi alegado.

### O adaptador

O adaptador (`chimera.eval.swe_bench`) é honesto sobre sua fronteira: as partes puras — a
invocação de `chimera solve` por instância (braço de tratamento) e o parsing do relatório de
avaliação oficial — vivem aqui e são testadas unitariamente; o dataset e o harness de avaliação
Docker são **opt-in e não vêm empacotados**, e o veredito pass/fail vem dos próprios testes do
SWE-bench, nunca autorreportado.

```bash
# 1. Curate a JSONL slice (one instance object per line): instance_id, repo, base_commit,
#    problem_statement, and (optionally) test_cmd. build_solve_command turns each into a
#    `chimera solve <issue> --verify <test_cmd> --repo-map --progress-ledger --replan --checklist`.
# 2. Run both arms through the official SWE-bench harness (model-only vs model+Chimera) on the
#    SAME instance ids, producing two evaluation reports.
# 3. Score the honest A/B:
chimera swe-bench-compare model_only_report.json chimera_report.json --instances mini.jsonl
```

Os dois relatórios são projetados sobre a lista de instâncias compartilhada (um id ausente conta
como não resolvido), então os dois braços são sempre comparados sobre instâncias idênticas — e
então o mesmo veredito de Newcombe-CI se aplica.

## Pontuando o A/B (sem precisar de benchmark)

Uma vez que cada braço produziu pass/fail por tarefa, a estatística é um único comando — isto não
precisa de **nenhum extra**, então o motor de relato honesto está sempre disponível:

```bash
chimera bench-compare baseline.json chimera.json --treatment-name chimera
```

Cada arquivo é uma lista JSON de booleanos (ou `{task_id: bool}`) sobre os **mesmos** IDs de
tarefa. Saída: a taxa de aprovação de cada braço limitada por Wilson, o delta, seu IC de Newcombe
de 95%, e se a diferença é **significativa** (o IC exclui zero). Se não for significativa, isso é
reportado sem rodeios — um subconjunto maior / mais seeds, ou a funcionalidade genuinamente não
move o número.

Esse mesmo `bench-compare` é a régua de medição para toda funcionalidade futura: cada adição do
M14 precisa mostrar que move o Δ no subconjunto idêntico, ou é cortada.

## A armadilha honesta (o que evitar)

- **Contaminação** — o SWE-bench público tem vazamento de solução documentado; prefira conjuntos
  resistentes a contaminação e reporte a ressalva.
- **Confusão de scaffold** — nunca reporte um "marcamos X%" cru; só o delta do A/B isola a
  contribuição do Chimera.
- **Baseline errado / cherry-picking** — compare fraco+Chimera com o *mesmo modelo fraco sozinho*,
  nos IDs de tarefa *idênticos*, com seeds e logs completos. Um modelo de fronteira é um teto, não
  um rival.
