# Linha de base de tokens e custo — leitura 1, só desktop, antes do P0-10

Pré-registro: [`PREREGISTRATION.md`](PREREGISTRATION.md) (commitado antes deste arquivo). Script:
`scripts/token_baseline.py` (lógica em `chimera/eval/token_baseline.py`). Nenhuma chamada de modelo; nenhum
dado bruto neste repositório, só agregados.

**Leitura 1, só desktop, antes do P0-10.** Isto é uma linha de base provisória, não uma medida de economia.

**Ressalva de ordem:** o pré-registro foi escrito depois de uma execução exploratória do script sobre estes
mesmos dados (incluindo janela de 30 dias), e isso está declarado no cabeçalho dele. Esta leitura foi feita
com as regras já fixadas, mas não é uma leitura "às cegas". Refazer a janela de 7 dias numa data posterior
ao commit do pré-registro remove a ressalva.

## O que está pendente (declarado, não escondido)

- **(a) Os dólares são provisórios.** Vêm de registros que o P0-10 ainda vai corrigir. O aceite "o total
  bate com a tela corrigida em menos de 1%" está **PENDENTE** até o P0-10. O que se confirmou abaixo é outra
  coisa: o total bate com o painel **de hoje** (mesma régua), não com o painel corrigido.
- **(b) Os 7 dias da VPS estão PENDENTES.** Esta leitura não olhou a VPS.
- **(c) Chat não tem número.** `usage.jsonl` não marca o chat (chat e Code gravam id simples), e o armazém
  de conversas de chat não existe nesta máquina. Chat e `solve` saem "não disponível nesta fonte".

## Onde foi lido

- **Home usado** (o que o desktop realmente usa; o Tauri define `CHIMERA_HOME` como `<pasta de dados do
  app>/data`, em `apps/desktop/src-tauri/src/main.rs`): `%APPDATA%/app.chimera.desktop/data`.
  `~/.chimera` existe nesta máquina mas só tem `approvals`, `code_sessions`, `history.db`, `repomap` e
  arquivos de ponte; não é onde o app grava.
- **Janela:** 7 dias, de `2026-11-06T02:41:35Z` a `2026-11-13T02:41:35Z` (UTC, com fuso explícito).
- **Encontrados:** `usage.jsonl`, `runs.jsonl`, `traces.jsonl`, `traces.jsonl.1`,
  `scheduler/cron_traces.jsonl`, `code_sessions/` (só os nomes; 3.725 ids de sessão Code).
- **Não encontrado:** `scheduler/cron_traces.jsonl.1` (não houve rotação).
- Diagnóstico de tempo: 0 `ts` sem fuso e 0 `ts` inválido nas três fontes. Fora da janela: 10.115 linhas de
  uso/recibo, 2.614 traces e 158 runs.

## Resultado: janela de 7 dias (a linha de base pré-registrada)

Esta máquina quase não foi usada nessa semana: **60 linhas** de uso, das quais **34 turnos**.

| Superfície | Turnos (n) | Resultado |
|---|---:|---|
| **Total** | **34** | números abaixo (n ≥ 30) |
| Code | 27 (+26 linhas auxiliares) | **amostra insuficiente** (só o n) |
| cron | 7 | **amostra insuficiente** |
| Design | 0 | **amostra insuficiente** |
| run autônomo | 0 | **amostra insuficiente** |
| agents / orquestração / explore | 0 / 0 / 0 | **amostra insuficiente** |
| não classificada | 0 | **amostra insuficiente** |
| chat | — | não disponível nesta fonte |
| solve | — | não disponível nesta fonte (indistinguível dos runs autônomos) |

Traces na janela (métricas por passo): cron 7, não classificada 25, run autônomo 0 — **todos abaixo de 30,
amostra insuficiente**. Recibos de run: cron 7 e run autônomo 0, também insuficientes para US$ por run
resolvido.

**Total (34 turnos, 60 linhas):**

- Tokens de prompt por turno: **2.052.756** (n = 34). Um turno é a soma de todos os passos do laço; o do Code
  carrega o contexto inteiro a cada passo, por isso o número é de milhões.
- Totais das 60 linhas: **69.825.959** tokens de prompt e **599.201** de completion; **US$ 3,0650** (todas
  as 60 têm preço: cobertura 60/60).
- US$ por turno: **0,0900** (n = 34, cobertura de preço 60/60).
- Taxa de cache: **amostra insuficiente** (n = 0). **Cobertura de cache 0/60**: nenhuma linha da janela
  reportou cache, então nada se afirma sobre cache nesta semana.
- Chamadas por papel (`route_kind`): single 34, extração de memória 26. Worker / planner / juiz **não
  disponíveis nesta fonte** (não são campo de `usage.jsonl`).
- Parte do custo que não é worker (`overhead_usd` das tentativas de run): 7 tentativas, cobertura 7/7,
  **amostra insuficiente**.
- Truncamentos: nenhum grupo com 30 traces; não se afirma nada.

## Conferência cruzada (mesma régua)

O script e o painel (`load_usage` + `usage_from_runs`, mesma janela, mesma junção `_already_counted`, depois
`summarize_usage`) **batem exatamente**, nas duas janelas lidas:

| | script | painel |
|---|---:|---:|
| turnos (7 dias) | 34 | 34 |
| tokens de prompt (7 dias) | 69.825.959 | 69.825.959 |
| tokens de completion (7 dias) | 599.201 | 599.201 |
| US$ (soma dos preços presentes) | confere | confere |

Divergência: **nenhuma** nos totais. Uma diferença de definição conhecida, não divergência de dado: o painel
calcula cache como `cache_read / (prompt + cache_read)` e este script como `cache_read / prompt`, porque o
passo do trace documenta `cached_tokens` como parte do prompt. Na janela de 7 dias nenhum dos dois tem
cobertura; na de 30 dias ambos dão 0,0.

## Leitura informativa, fora do pré-registro: janela de 30 dias

A janela de 7 dias ficou quase toda abaixo do corte. Para dar uma ordem de grandeza, o mesmo script foi
rodado com `--since-days 30` (`2026-10-14` a `2026-11-13`). **Não é a linha de base pré-registrada e não
substitui a leitura de 7 dias**; é contexto, com a mesma conferência cruzada (também bate: 4.673 turnos,
5.408.294.846 tokens de prompt, 80.941.449 de completion).

| Superfície | Turnos | Prompt/turno | US$/turno | Cobertura de preço | Cobertura de cache |
|---|---:|---:|---:|---:|---:|
| Total | 4.673 | 1.155.672 | 0,0340 | 9.201/9.202 | 912/9.202 |
| Code | 4.462 | 1.141.687 | 0,0339 | 8.898/8.898 | 758/8.898 |
| não classificada | 140 | 2.114.454 | 0,0475 | 233/233 | 145/233 |
| run autônomo | 40 | 234.537 | 0,0264 | 40/40 | 0/40 |
| cron | 29 | amostra insuficiente | | | |
| Design / agents | 1 / 1 | amostra insuficiente | | | |

- US$ total das linhas com preço em 30 dias: **US$ 160,00** (uma linha sem preço, fora do total e contada).
- Taxa de cache onde há cobertura (912 linhas): **0,0**, isto é, o provedor reportou cache zero nessas linhas.
  Cobre só ~10% das linhas; **o resto não reportou nada**, e isso não é falha de cache.
- Traces da superfície "não classificada" (2.557): **17,2 passos por turno** e **34.208 tokens de prompt por
  passo**; 1 passo cortado e 8 chamadas de ferramenta descartadas.
- Parte não-worker do custo das tentativas de run: **US$ 0,49** em 69 tentativas (cobertura 69/69).

## Achados fora do pré-registro

1. **`_log_usage` do Code não passa o cache.** Em `chimera/api/code_api.py` ela constrói a `UsageRecord`
   sem `cache_read_tokens`, e o Code é 95% dos turnos; a cobertura de cache do Code é de 758 em 8.898
   linhas (a origem dessas 758 não foi investigada). O cache está medido nos passos do trace
   (`cached_tokens`), mas a linha de uso quase não o carrega. Candidato para o P1; esta leitura não
   corrige nada.
2. **"Não classificada" é grande na janela de 30 dias** (140 turnos, 2.557 traces). Atribuição de superfície
   é o ponto fraco, como o plano já previa: chat não tem marca. Isto é resultado, não defeito escondido.
3. Esta máquina teve 34 turnos na semana de 2026-11-06 a 2026-11-13 e 4.673 nos 30 dias; o uso é muito
   irregular, e uma janela de 7 dias única não representa a média.

## O que esta leitura não diz

Não diz que algo ficou mais barato, não diz nada da VPS, nada de chat, nada de qualidade, e nada causal. Os
números de dólar podem mudar com o P0-10.

## Como reproduzir

```bash
python scripts/token_baseline.py --since-days 7 --now 2026-11-13T02:41:35+00:00 --json
```

O script só abre arquivos para leitura; o resultado depende dos arquivos do home naquele momento.
