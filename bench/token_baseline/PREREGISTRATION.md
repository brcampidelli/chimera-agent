# Pré-registro — linha de base de tokens e custo (leitura 1, só desktop)

**Registrado em 2026-11-12, DEPOIS de uma execução exploratória do script sobre os dados reais.** Item
P0-18 do plano de auditoria. Este arquivo é commitado antes de `RESULTS.md` e não é afrouxado depois: o que
a leitura mostrar entra em `RESULTS.md`, o que este arquivo não previu entra como "achado fora do
pré-registro".

Ordem real, sem retoque: (1) o esquema de cada arquivo foi lido no código e o tamanho dos arquivos do home
foi visto; (2) o script foi rodado **algumas vezes sobre os dados reais** (inclusive numa janela de 30 dias)
só para checar que funcionava, e nessas passadas vi totais, entre eles os de uma superfície com 27 turnos;
(3) só então este arquivo foi escrito e as regras abaixo foram fixadas. Portanto **o pré-registro não
antecede toda leitura**: antecede a leitura oficial (janela de 7 dias, gravada em `RESULTS.md`), não a
exploração. O que mudou por causa da exploração está listado na seção final, e é só o que fecha uma
leitura mais estrita do corte de 30 turnos; nenhuma métrica foi trocada nem corte afrouxado.

## Por que existe

Nenhuma economia da onda P1 pode ser afirmada sem uma linha de base: "gastamos menos" precisa de um
"gastávamos tanto" medido com a mesma régua. Esta é a primeira leitura, feita por um script somente-leitura
sobre os dados locais, sem chamar modelo.

## Fontes e o que cada uma é

| Arquivo (no home) | Escritor | Unidade | Usado para |
|---|---|---|---|
| `usage.jsonl` | `chimera/api/usage.py` (`UsageRecord`) | uma linha por turno **ou** por chamada auxiliar | turnos, tokens, cache, US$, papel (`route_kind`) |
| `runs.jsonl` | `chimera/api/runs.py` (`RunReceipt`) | uma linha por run autônomo, com tentativas | US$ por run resolvido; turnos de run (via `usage_from_runs`) |
| `traces.jsonl` (+ `.1`) | `StepLog.write` | uma linha por `agent.run`, com os passos | passos por turno, prompt por passo, truncamentos |
| `scheduler/cron_traces.jsonl` (+ `.1`) | `job_runner.py` → `StepLog.write` | idem, só cron | idem, superfície cron |
| `code_sessions/` | Code | **só os nomes** dos arquivos `<id>.json` | atribuir turnos de id simples à superfície Code |

O esquema foi lido no código, não inferido do dado. `.env` nunca é aberto. Nenhum arquivo é criado,
movido, apagado ou rotacionado no home; todos são abertos em modo leitura.

## Janela

`[agora − 7 dias, agora]`, com `agora` fixado uma vez no início da execução, em UTC, e gravado em
`RESULTS.md`. O filtro compara instantes **com fuso**: `ts` sem fuso é lido como UTC e contado em
`ts_sem_fuso`; `ts` que não se lê é excluído e contado em `ts_invalido`. Nunca se compara texto.

## Unidade "turno" e a mesma régua do painel

A régua é a do painel de custo (`summarize_usage`): as linhas de `usage.jsonl` mais as de
`usage_from_runs(runs.jsonl, already=_already_counted(...))` — esta última é o que impede contar duas
vezes um run que está nos dois arquivos. **Turno** é toda linha cujo `route_kind` não está em `NOT_A_TURN`
(memória, memory_tidy, side, side_estimated). Linhas de `NOT_A_TURN` somam tokens e dólares, e entram em
"linhas auxiliares", mas não são turno. Cada tentativa de um run autônomo é uma linha, como no painel.

## Atribuição de superfície

Pelo campo, nunca por palpite. Regras na ordem; a primeira que casa vale.

1. `session_id` começa com `cron:` → **cron**.
2. começa com `design:` → **Design**.
3. começa com `run:` (linha reconstruída de `runs.jsonl`) → **run autônomo**. `solve`, o run do Code e o
   endpoint de run gravam o mesmo recibo e **não se separam** nesta fonte; não se chuta qual foi.
4. começa com `agents:`, `orchestration:` ou `explore:` → a própria classe, com esse nome.
5. id simples (sem `:`) cujo `<id>.json` existe em `code_sessions/` → **Code**.
6. qualquer outra coisa → **não classificada**, com a contagem.

Consequência declarada: **chat** não tem marca própria em `usage.jsonl` (chat e Code gravam id simples) e o
armazém de conversas de chat não existe nesta máquina. Um id simples sem arquivo em `code_sessions/` pode
ser chat, terminal ou uma sessão Code apagada; vai para "não classificada", e o chat fica como **"não
disponível nesta fonte"**. Se a classe "não classificada" ficar grande, isso é o resultado.

Para os recibos de `runs.jsonl`: um run é **cron** se alguma tentativa tem `run_id` presente em
`cron_traces.jsonl`; senão, **run autônomo**.

Para os traces: `cron_traces.jsonl` → cron; linha de `traces.jsonl` cujo `run_id` é o de alguma tentativa
em `runs.jsonl` → run autônomo; o resto de `traces.jsonl` → não classificada. Trace e usage **não se unem**
por turno (o trace não tem `session_id`); as métricas por passo valem para a superfície do trace, não para
o número de turnos de `usage.jsonl`.

## Métricas

Por superfície, na janela:

- **turnos**: contagem de linhas-turno.
- **tokens de prompt por turno**: média sobre as linhas-turno (uma linha de uso soma os passos do turno).
- **tokens de prompt por passo** e **passos por turno**: dos traces (`steps[].prompt_tokens`, `len(steps)`),
  só onde o trace é atribuível.
- **chamadas por papel**: contagem de linhas por `route_kind` (single, fusion, cascade, hierarchy, extração
  de memória, tidy, side). Worker / planner / juiz **não existem como campo** em `usage.jsonl`: declarados
  "não disponíveis nesta fonte". Como aproximação do que não é worker, mostra-se a soma de `overhead_usd`
  das tentativas em `runs.jsonl`, com a cobertura ao lado.
- **taxa de cache** = `Σ cache_read_tokens / Σ prompt_tokens`, **só** sobre linhas em que
  `cache_read_tokens` **não é None**; a **cobertura** (linhas com cache reportado ÷ linhas) vai ao lado.
  Esta é a definição desta leitura, porque o passo do trace documenta `cached_tokens` como a parte de
  `prompt_tokens` servida do cache. O painel usa `cache_read / (prompt + cache_read)`; `RESULTS.md`
  mostra o número do painel só na conferência cruzada e diz que as réguas diferem.
- **US$ por turno** = soma de `usd` das linhas-turno com preço ÷ quantidade delas; **cobertura de preço** =
  linhas com `usd` ≠ None ÷ linhas. `usd` None fica fora do numerador e do denominador e é contado. Zero
  nunca é inventado para preço ausente.
- **US$ por run resolvido**: de `runs.jsonl` na janela, `success` verdadeiro e `usd` ≠ None; vale a mesma
  cobertura. Os runs com `usd` None entram na contagem de "sem preço".
- **truncamentos**: `Σ truncated_steps` e `Σ dropped_tool_calls` dos traces; traces que não têm o campo
  (linha antiga) ficam fora e são contados.

Métrica que a fonte não permite calcular sai **"não disponível nesta fonte"**, nunca zero.

## Regra de amostra insuficiente

Qualquer grupo com **menos de 30** unidades sai **"amostra insuficiente"** com **apenas o n**: nenhum número
derivado, nem média, nem soma, nem taxa. Com 30 ou mais, sai o número. O corte é fixo e igual para todas as
superfícies, e vale também para o total e para "não classificada". Unidades, para não deixar folga:

- **superfície, nos dados de `usage.jsonl`**: 30 **turnos** (linhas auxiliares não contam). Abaixo disso a
  superfície mostra só `linhas`, `turnos` (com o n) e `linhas_auxiliares`; nenhuma soma, média ou taxa;
- **taxa de cache**: 30 linhas **com cache reportado**; **US$ por turno**: 30 turnos **com preço**;
- **métricas de trace**: 30 traces; **US$ por run resolvido**: 30 runs resolvidos com preço.

## Privacidade

A saída (texto, `--json` e `RESULTS.md`) só tem agregados. Nunca: chave, token, cabeçalho, texto de prompt,
resposta, tarefa, caminho de arquivo do usuário, id de sessão, nome de modelo, nome de job. O caminho do
home sai sem o nome de usuário (`%APPDATA%\...` ou `~\...`). Valores de campos livres (como `route_kind`)
só aparecem se estiverem num conjunto fechado conhecido; o resto cai em "outro".

## O que esta leitura NÃO concluirá

- **Nenhuma economia.** É uma linha de base: não há "antes e depois" ainda.
- **Os dólares estão provisórios.** Vêm de registros que o P0-10 ainda vai corrigir. O aceite "o total bate
  com a tela corrigida em menos de 1%" está **PENDENTE** até o P0-10.
- **A VPS não foi lida.** Os 7 dias da VPS estão **PENDENTES**; esta leitura é só desktop.
- **Nada sobre chat** como superfície, pela razão da atribuição acima.
- **Nada sobre qualidade.** Tokens e custo não dizem se a resposta foi boa.
- **Nada causal.** Uma semana de uso de uma pessoa numa máquina descreve aquela semana; não generaliza.
- **Sem comparação entre superfícies de tamanhos de amostra diferentes** como se fossem igualmente
  medidas: o n vai ao lado de cada número.

## Conferência cruzada (critério de aceite)

O total de turnos e de tokens do script, na mesma janela, é comparado com o que `summarize_usage` devolve
sobre as mesmas linhas (`load_usage` + `usage_from_runs`, mesma janela). Divergência é registrada e
explicada, não apagada.

## Como isto responde a `bench/PROTOCOL.md` §11 a §14

Nenhuma é "não se aplica por omissão"; cada uma diz por que cabe ou não.

- **§11 (intervalo).** Não se aplica: esta leitura é descritiva (contagens, somas, médias de um log), sem
  hipótese nem comparação entre braços, então não há intervalo a escolher. A proteção equivalente é o corte
  fixo de 30 unidades: abaixo dele nenhum número derivado sai. Nenhum bootstrap, nenhuma inferência.
- **§12 (margem de equivalência).** Não se aplica: nenhuma alegação de "sem diferença" é feita. Há uma
  linha de base, não um contraste.
- **§13 (controles).** Aplica-se, de duas formas. O controle de régua é a conferência cruzada com
  `summarize_usage` (acima), que deve bater nos totais; e o controle de aparato são os testes sintéticos
  (amostra de 29 e de 30, cache e preço ausentes, run em dois arquivos contado uma vez, segredo plantado,
  home intocado). Não há braço placebo porque nada é intervenção.
- **§14 (escopo de modelo).** Não se aplica: nenhum modelo é chamado e nenhum componente é removido ou
  recomendado. Os nomes de modelo nem entram na saída.

## Emendas feitas antes do commit deste arquivo (para não parecer que nada mudou)

A execução exploratória descrita no cabeçalho mostrou que o corte de 30 estava aplicado a linhas e não a
turnos, deixando uma superfície com 27 turnos exibir totais. As edições feitas depois dela (unidades
explícitas do corte; colocação dos recibos cron; a seção sobre o protocolo, exigida por teste do
repositório) **fecham a leitura mais estrita do que o texto original já dizia** ("superfície com menos de
30 turnos: só o n"); não afrouxam nenhum corte nem trocam métrica. A leitura oficial de `RESULTS.md` foi
feita depois dessas edições. Qualquer emenda posterior a este commit sai como emenda datada, nunca como
edição silenciosa.
