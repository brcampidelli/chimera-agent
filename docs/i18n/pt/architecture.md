---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — Arquitetura

Este documento mapeia a base de código para o design e para a pesquisa em que ela se apoia. Para
o "porquê", veja [VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## O núcleo de raciocínio: LLM-Fusion

`chimera/fusion/`

O motor de fusão executa uma tarefa através de um **painel** de modelos, faz um **juiz** produzir
uma análise estruturada (consenso / contradições / cobertura parcial / insights únicos / pontos
cegos) e então um **sintetizador** escreve a resposta final fundamentada nessa análise
(`FusionEngine`). Ele implementa o protocolo `SupportsComplete`, então é um backend de raciocínio
plug-and-play em qualquer lugar onde um modelo é esperado — inclusive dentro do loop do agente.

Um **roteador consciente de custo** (`RoutedBackend` + `RoutingPolicy`) mantém a fusão seletiva:
turnos de chamada de tool vão para um único modelo (a fusão não faz tool-calling), e só turnos de
raciocínio profundo / de alto risco passam por fusão. Inspirado no OpenRouter Fusion (o ganho vem
da etapa de *síntese*, não só da diversidade de modelos) e no AURORA-AI (orçamento adaptativo entre
modelos heterogêneos).

## O loop do agente & a autonomia Tier-2

`chimera/core/`

- `Agent` — um loop mínimo de ReAct / tool-calling com um **transcript explícito** (o estado vive
  fora do modelo). Depende só de `SupportsComplete` + um `ToolRegistry`.
- `AutonomousAgent` — Tier-2: monta o contexto **Spine** com escopo de propriedade → **planeja** →
  snapshot → executa → **revisão do Manager** (gerar-vs-verificar) → **verificar-ou-reverter** →
  tenta de novo com feedback, registrando cada tentativa no buffer de experiência.
- `WorkspaceGuard` — snapshot/restauração de arquivo de texto, o mecanismo por trás do
  verificar-ou-reverter.
- `CommandVerifier` — "evidência executável" (exit 0 == sucesso).

### Atacando a degradação da evolução contínua

O problema em aberto (segundo *Agentic Software*, `2606.05608`): a performance cai de >80% em
tarefas isoladas para ~38% em evolução contínua — contexto de longo horizonte + propagação de
erro. As contramedidas do Chimera, cada uma fundamentada na literatura:

| Contramedida | Onde | Base |
|---|---|---|
| Externalizar o estado (transcript/workspace, não o contexto do LLM) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Contexto com escopo de propriedade (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Supervisão gerar-vs-verificar | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verificar-ou-reverter | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Buffer de experiência (falhas como negativos) | `evolution/experience.py` | HORIZON `2606.28279` |
| Consolidação de mensagens em times | `orchestration/comms.py` | MOC `2606.02359` |
| Benchmark de evolução contínua | `eval/continuous.py` | Enunciado do problema do EvoClaw |

## Memória & auto-evolução

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — itens hierárquicos (working / episodic / semantic / persona) com
  `ADD / UPDATE / DELETE / NOOP` (`remember`) e deduplicação por `merge` (Memory-R1, `2606.14502`).
- **Skill evolver** — o `SkillEvolver` propõe uma `LearnedSkill` reutilizável a partir de um
  sucesso, testa e só mantém se passar (propor → testar → manter/descartar). Skills aprendidas
  são **templates de prompt, não código executável** — seguro de autorar de forma autônoma antes
  de qualquer auto-modificação em nível de código. O refinamento melhora um template a partir de
  suas falhas (VIBEMed `2606.15504`).
- **Crons auto-aprendidos** — o `CronLearner` detecta tarefas recorrentes e propõe crons
  (`created_by=agent`, **desabilitados** até aprovação humana).
- **Benchmark de evolução contínua** — roda uma cadeia de tarefas por um solver e reporta a
  degradação (taxa geral de aprovação, primeira metade vs. segunda metade, maior sequência).

## Governança & segurança

`chimera/governance/`

Um kernel de confiança auto-aperfeiçoável (AgentTrust v2, `2606.08539`):

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. O `RuleSet` léxico trata
  ameaças de assinatura fixa de forma determinística; um **juiz semântico** opcional trata a
  intenção; regras destiladas tornam isso mais barato com o tempo. Invariante: **nunca bloquear de
  forma dura uma ação benigna**.
- `SkillValidator` / `ScheduleValidator` — a **superfície de edição restrita e
  estaticamente verificável** para auto-modificação (AutoMegaKernel `2606.09682`): propostas
  inseguras são rejeitadas antes mesmo de rodar.
- `AuditLog` — JSONL append-only de decisões e mudanças de evolução.
- `GovernedTool` / `govern_registry` — envolve qualquer tool para que sua execução seja
  controlada; compõe com o loop de agente existente sem alterá-lo (`chimera ... --guard`).

### A camada de taint (contenção de prompt-injection)

Sobreposta ao kernel — heurística, honesta, e nunca uma fronteira dura (o sandbox é essa
fronteira):

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — um ledger de capacidades por
  execução. Um fetch contamina seu conteúdo; uma escrita/execução que consome conteúdo contaminado
  **escala para review** (`assess_action`). Conteúdo buscado não confiável é retornado
  **cercado como dado** e com tokens de controle de chat-template removidos (`sanitize.py`), e
  artefatos duráveis de uma execução contaminada mantêm proveniência `tainted` para que o veneno
  não consiga se lavar em uma memória/skill "limpa".
- `AggregateMonitor` (`aggregate_monitor.py`) — um monitor um nível acima: dado os eventos de
  capacidade de cada sub-agente, ele captura **fluxos divididos** que um monitor por-agente não
  consegue ver (o agente A busca conteúdo não confiável, o agente B executa ou **exfiltra**).
- `check_drift` (`drift.py`) — uma `Spec` de requisitos executáveis (`defines`/`contains`/`absent`/
  `command`) que serve tanto como referência de verdade para `solve --verify` quanto como a
  autoridade do orquestrador de projeto sobre o que significa "pronto" (abaixo). Verificações
  negativas falham de forma fechada em arquivos que não conseguem escanear.
- `QuarantineTool` + allowlist adaptativa (`quarantine.py`, `allowlist.py`) — um leitor
  quarentenado dual-LLM/CaMeL e uma allowlist de tools adaptativa a taint que se estreita assim
  que uma execução é contaminada.

## Times multi-agente (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — especialização de papel (estilo CrewAI).
- `SequentialCrew` — papéis em ordem, cada um vê as saídas anteriores **consolidadas** e pode
  escrever na memória compartilhada.
- `SupervisorCrew` — trabalhadores endereçam a tarefa em paralelo, as saídas são consolidadas, e
  um supervisor sintetiza (estilo CAPRA `parallel_review`, `2606.18976`).
- `consolidate` — a fusão de mensagens do MOC mantém o contexto do time enxuto (`2606.02359`).

## Ecossistema auto-evolutivo (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — projeta/constrói/avalia agentes especializados (agentes construindo agentes).
  Duas salvaguardas do Meta-Agent Challenge (`2606.04455`): **isolamento de tools** (as tools de
  um agente projetado são filtradas para uma lista permitida) e **separação de testes ocultos**
  (passar no visível + falhar no oculto ⇒ suspeita de reward-hacking, não creditado como sucesso).
- `ChangeQueue` — governa o *ritmo* das mudanças (fila de merge FIFO + limites de lote), não o
  headcount ("Govern the Repository", `2606.28235`).
- `TrajectoryCollector` — registra (prompt, resposta, resultado) e exporta datasets **SFT / DPO**.
  O fine-tuning propriamente dito é **opt-in e externo** — o Chimera coleta, ele não treina.

## Economia de custo & a hierarquia de delegação

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

A delegação só compensa quando é mais barata do que fazer o trabalho inline, e a alegação é
**medida, não afirmada**:

- `HierarchicalOrchestrator` — decompõe → despacha trabalhadores com orçamento → verifica cada
  resultado → sintetiza. Fan-out no formato de leitura delega; uma subtarefa trivialmente pequena
  é respondida inline pelo modelo de confiança do topo.
- `CascadeBackend` — fraco → gate → médio → gate → fusão, subindo de nível só quando a resposta de
  um tier falha em um gate de aceitação barato. O **route log** registra cada salto, então o custo
  é a **soma de todos os saltos tentados**, não só o aceito — as escaladas são pagas.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — um teto rígido de tokens aplicado no
  backend, por trabalhador.
- `EnvelopeVerifier` — schema → critérios de aceitação → checagem pontual probabilística (avalia
  a fidelidade de um resumo contra o artefato bruto); uma nova pergunta disparada por uma falha na
  checagem pontual é reauditada.
- **Recibos de delegação** (`receipts.py`) — cada delegação registra seus tokens/custo medidos **e
  o contrafactual inline na mesma linha**, precificado na tarifa própria de cada modelo (modelo
  desconhecido → `None`, nunca fabricado). O overhead de decompor/sintetizar do próprio
  orquestrador também é medido, então `summarize_delegations` (`chimera delegations`) reporta uma
  economia líquida **auditável**, e `cascade-bench` reporta a **cauda** de custo (p50/p95/p99), não
  só a média.

## O flywheel de auto-evolução

`chimera/evolution/`

O "treino" que nunca toca nos pesos — sinalizado por fitness, sem gradiente, e reversível:

- `EvolutionContext` — o conjunto compartilhado (experience, trajectories, memory, auto-evolver,
  skill cards, playbook) que torna o aprendizado uma propriedade da *pilha* do agente, não só do
  comando `solve`.
- Skill cards + refinamento **GEPA**, o **playbook** ACE, e uma `SkillLifecyclePolicy` que
  promove/rebaixa uma skill pelas suas estatísticas **medidas** de uso/sucesso (uma skill nova
  nasce `provisional`).
- O **diff-gate** — um "sucesso oco" (verificador passou mas o diff do workspace está vazio) não
  gera uma skill ou memória; o flywheel só aprende com trabalho que de fato aconteceu.
- O **transfer-gate** (`eval/transfer.py`) — um artefato ajustado só é promovido se também se
  sustentar em um holdout, protegendo contra transferência negativa. `maturity.Scorecard.weakest()`
  é o objetivo: o loop mira a capacidade mais fraca. Regressões só fazem rollback automático diante
  de uma queda **estatisticamente significativa** (um IC, nunca um único ponto).

Toda mudança de um default passa por um A/B pareado **pré-registrado** (`bench/`), publicado quer
vença quer perca — sem re-rodar em busca de significância.

## Autonomia de projeto (do início ao fim)

`chimera/orchestration/project.py`

O `ProjectOrchestrator` executa um projeto inteiro contra uma `Spec`: grafo de tarefas (um DAG
estilo Kanban com `depends_on`) → cada card pronto é resolvido (com o contexto de evolução acima)
→ **aceito contra a Spec** via `check_drift` (a única autoridade sobre "pronto") → requisitos não
atendidos geram os próximos cards, repetindo até a Spec estar alinhada ou um orçamento /
máximo de iterações / checkpoint humano interromper. Passos de risco (`risk: high` — deploy /
migração / delete) **pausam para aprovação humana**; a execução é durável e retomável.

## Transversal

- **Providers** (`providers/`) — um gateway único, agnóstico de provedor, sobre o LiteLLM; as
  chaves podem viver em `.env` e são exportadas para o ambiente para que o LiteLLM as veja.
- **Tools** (`tools/`) — primitivas nativas; os metadados de tool são atributos de instância para
  que tools geradas dinamicamente (OpenAPI/MCP) funcionem.
- **Integrations** (`integrations/`) — cliente MCP (extra opcional `mcp`) + importador
  OpenAPI→tool + registro de conectores.
- **Scheduler** (`scheduler/`) — crons + SOPs de evento; o tempo é injetado para testes
  determinísticos.
- **Migration** (`migration/`) — importa config + skills + faz **merge** da memória de longo prazo
  do Hermes / OpenClaw, deduplicada e não-destrutiva.

## Filosofia de testes

Todo subsistema é testado unitariamente com **backends falsos** — determinísticos, sem rede, sem
chaves. Comandos que de fato chamam um LLM têm smoke test para seu caminho de falha sem chave. O
quality gate (`ruff` + `mypy --strict` + `pytest`) roda no CI em Python 3.11 e 3.12.
