<div align="center">

<img src="assets/logo-wide.png" alt="Logo do Chimera" width="460" />

# Chimera

**Um agente de IA open-source e autoevolutivo cujo núcleo de raciocínio é um motor de Fusão de LLMs.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-entrar-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <b>Português</b> · <a href="README.es.md">Español</a> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

O Chimera funde **múltiplas LLMs por requisição** — um pipeline **painel → juiz → sintetizador**
inspirado no OpenRouter Fusion — em vez de depender de um único modelo de fronteira, e
**se aperfeiçoa ao longo do tempo** (memória → skills → modelo), resistindo à
*degradação de evolução contínua* que limita os agentes atuais.

> **Status:** desenvolvimento inicial (0.1.x). O plano de construção completo (M0–M7) está
> implementado — Tiers 1–4 + o motor de Fusão + autoevolução multinível + um kernel de
> governança — mais um **loop de aprendizado comportamental fechado**, uma **camada
> operacional** (Kanban + worker lanes, crew SDLC, um DSL declarativo de loops),
> **isolamento de execução** (sandbox Docker + git worktrees) e as **técnicas dos papers**
> em que foi baseado (HORIZON, VIBEMed, Spec Growth, AgentTrust v2, AutoMegaKernel,
> Meta-Agent, MOC).
> 300 testes (+ integração ao vivo opt-in) · `mypy --strict` limpo · `ruff` limpo.

---

## Por que o Chimera

Os frameworks existentes são fortes em um único eixo: Hermes/OpenClaw evoluem skills mas rodam
um único modelo; CrewAI/LangGraph orquestram bem mas não aprendem; TrustClaw/NemoClaw/ZeroClaw
trazem segurança/sandbox mas não evoluem. **O Chimera combina os quatro:**

- 🧬 **Fusão como raciocínio** — o motor painel→juiz→sintetizador é o núcleo de raciocínio, não um add-on. O ganho vem do próprio passo de *síntese*, não só da diversidade de modelos.
- 🪜 **Quatro níveis de capacidade numa progressão** — ferramentas aumentadas → autônomo de tarefa única → equipes multiagentes → ecossistema autoevolutivo.
- ♻️ **Um loop de autoevolução multinível fechado** que ataca explicitamente a degradação de evolução contínua (estado externalizado, contexto resistente a drift, verify-or-revert, buffer de experiência realimentado no planejamento).
- 🛡️ **Um kernel de governança que também se aperfeiçoa** — allow/warn/block/review, com superfície de automodificação validada estaticamente e precedente guardado.

## Recursos

**Raciocínio & autonomia**
- **Motor LLM-Fusion** — painel provider-agnostic de modelos de fronteira + abertos, um juiz que revela consensos/contradições/pontos cegos, e um sintetizador; um **roteador custo-consciente** funde só quando compensa (turnos de ferramenta seguem modelo único).
- **Autonomia Tier-2** — planejar → executar → revisão do Manager → **verify-or-revert** (snapshot/restauração do workspace + verificador por comando), com **isolamento em git worktree** (`solve --isolate`) — as edições só entram se verificadas.
- **Crew de ciclo de vida SDLC** (`chimera lifecycle`) — pipeline pré-montado **plan → build → test → review** com verify-or-revert no estágio de teste.
- **Equipes multiagentes** — especialização por papéis, crews sequenciais e supervisor, consolidação MOC, memória compartilhada, revisão paralela.

**Autoevolução & governança**
- **Loop comportamental fechado** — falhas passadas alimentam o planner (lições), sucessos verificados auto-escrevem memória, e tarefas recorrentes auto-evoluem uma skill validada e smoke-testada — tudo gated por verify-or-revert. Mais benchmark de evolução contínua e stress test EvoClaw naive-vs-guarded.
- **Memória hierárquica** — working / episodic / semantic / persona **+ camada graph** (`memory graph`) que recupera fatos por entidade, não só keyword.
- **Evolução de modelo opt-in** — `solve` coleta trajetórias; `evolve` cura datasets SFT/DPO e emite uma receita LoRA executável. O treino fica externo/opt-in.
- **Kernel de governança** — allow/warn/block/review (regras léxicas + juiz semântico opcional, com destilação de regra e **precedente guardado**), validador estático da superfície de automodificação, log de auditoria append-only, ferramentas governadas, **modelo de mudança de 4 atores**, e **drift gate spec↔código** (`chimera drift`).

**Provedores**
- **Qualquer modelo, uma interface** — provider-agnostic via LiteLLM (100+ modelos por slugs `provider/model`); chaves first-class para OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted & resiliente** — endpoints custom para **Ollama/vLLM** (`CHIMERA_API_BASE`), **cadeias de fallback**, **credential pools** com rotação round-robin, troca de modelo **`/model`** ao vivo, e **prompt caching** (`CHIMERA_CACHE`) para turnos de raciocínio repetidos.

**Orquestração, interfaces & integrações**
- **Kanban + worker lanes** (`chimera kanban`) — quadro de tarefas (backlog → doing → review → done) onde os cards despacham para a lane `solve` ou `crew`; `kanban learn` transforma tarefas recorrentes em cards.
- **Loop Engineering** (`chimera workflow`) — escreva um loop autônomo em YAML (steps que `usam` a stack, com condições `when` e laços `repeat`/`until`).
- **Interfaces** — REPL `chat`, **TUI** full-screen (Textual) e um **gateway de mensageria** HTTP com uma conversa (e memória) por chat.
- **Sandbox de execução** — rode o shell localmente ou em um container **Docker** isolado (`CHIMERA_SANDBOX=docker`).
- **Integrações** — cliente **MCP** (stdio) first-class + importador **OpenAPI/REST → tool**; **crons** (atribuídos e auto-aprendidos, com confirmação); **migração** de config/skills/memória de longo prazo do Hermes Agent / OpenClaw.

**Extras embutidos**
- **Vision** (colar imagem), **Modo Entregável** (artefatos polidos) e um **Pet** companheiro — mais slots de credencial pré-set para busca web, geração de imagem, TTS/voz e mais (`chimera features`).

## Início rápido

Requer Python **3.11+** (3.12+ recomendado) e [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # defina ao menos uma chave de provedor (OpenRouter recomendado)
uv run chimera doctor       # verifique seu ambiente
```

## Comandos

```bash
chimera doctor / models / features    # status, configuração, capacidades opcionais
chimera chat                          # assistente multi-turno interativo (seu braço-direito)
chimera tui                           # app full-screen no terminal (Textual)
chimera serve                         # servidor HTTP do gateway de mensageria (sessões por chat)
chimera run "PROMPT" --image pic.png   # Tier-1 single-shot (com visão via --image)
chimera deliver "um plano" -o plan.md   # Modo Entregável: produz um artefato polido
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: painel -> juiz -> sintetizador
chimera solve "TAREFA" --verify "pytest -q" --isolate   # Tier-2: verify-or-revert, isolado em git worktree
chimera lifecycle "TAREFA" --verify "..."   # crew SDLC: plan -> build -> test -> review
chimera workflow flow.yaml             # roda um loop declarativo (Loop Engineering)
chimera crew "TAREFA" --mode supervisor  # crew multiagente Tier-3
chimera meta "um agente para X"          # meta-agente Tier-4: projeta um agente especializado
chimera kanban add/board/run/learn     # quadro de tarefas com worker lanes (solve/crew)
chimera drift spec.yaml                # drift gate spec<->código (sai 1 em drift)
chimera memory add / graph             # memória de longo prazo curada + grafo entidade-relação
chimera cron add / learn               # tarefas agendadas (atribuídas + auto-aprendidas, confirmadas)
chimera bench                          # benchmark de evolução contínua
chimera migrate hermes ~/.hermes --apply   # importa config + skills + mescla memória
chimera evolve status / recipe         # evolução de modelo opt-in: dados SFT/DPO + receita LoRA
chimera pet new --name Chimi           # adote um companheiro virtual
```

Veja o **[Guia de Uso](docs/usage.md)** para instalação, configuração e cada comando com exemplos copy-paste.

## Arquitetura

```
chimera/
  core/          loop do agente (ReAct) + autonomia Tier-2 (plano, verify-or-revert) + isolamento git worktree
  fusion/        painel -> juiz -> sintetizador + roteador custo-consciente
  memory/        working / episodic / semantic / persona + camada graph + Memory Manager
  skills/        biblioteca embutida + recuperação de skill-context
  evolution/     evolutor de skills, hook de auto-evolução, buffer de experiência
  governance/    kernel de confiança (regras + juiz + precedente guardado), validador estático, drift gate, modelo de 4 atores, auditoria
  orchestration/ papéis, crews sequenciais/supervisor, comms MOC, crew de ciclo de vida SDLC
  ecosystem/     meta-agente, governança de tempo de mudança, coleta de trajetórias, evolução de modelo
  kanban/        quadro de tarefas + worker lanes (despacho para crews / solve)
  workflow/      DSL declarativo de loops (Loop Engineering)
  tools/         ferramentas nativas (arquivos, shell, http)
  sandbox/       backends de execução (local / docker isolado)
  integrations/  cliente MCP (stdio) + importador OpenAPI->tool
  scheduler/     crons (atribuídos + auto-aprendidos) + engine de SOP
  migration/     importa do Hermes/OpenClaw (config, skills, merge de memória)
  providers/     gateway de LLM (LiteLLM) — fallback, credential pools, endpoints custom, prompt cache
  interface/     ChatSession conversacional (compartilhada por chat, TUI, gateway)
  tui/  server/   app Textual full-screen · gateway de mensageria + transporte HTTP
  eval/          evolução contínua + stress test EvoClaw + cenários diários
  cli/           o comando `chimera` (CLI-first)
```

Veja [docs/architecture.md](docs/architecture.md) para o design completo e a pesquisa em que se baseia.

## Roadmap

| Marco | Status |
|---|---|
| M0–M7 — Tiers 1–4 + Fusão + autoevolução + governança | ✅ |
| M8 — Interfaces (chat/TUI/gateway), stress-test EvoClaw, evolução de modelo opt-in | ✅ |
| Camada de provedores — endpoints self-hosted, fallback, credential pools, `/model`, prompt cache | ✅ |
| Loop comportamental fechado — experiência→planner, auto-memória, auto-skill (governado) | ✅ |
| Orquestração operacional — Kanban + worker lanes, crew SDLC, Loop DSL | ✅ |
| Isolamento de execução — sandbox Docker + git worktrees | ✅ |
| Técnicas dos papers — HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |

A seguir: validação de evolução contínua mais profunda em escala, logins OAuth de provedor e um
backend opcional de durabilidade LangGraph. O treino de modelo (LoRA/DPO) segue externo/opt-in por design.

## Desenvolvimento

```bash
uv run ruff check .      # lint
uv run mypy chimera      # type-check (strict)
uv run pytest -q         # testes
```

Veja [CONTRIBUTING.md](CONTRIBUTING.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Questões de segurança: veja [SECURITY.md](SECURITY.md).

## Comunidade

Participe da conversa no **[Discord](https://discord.gg/ACvBbrmguV)** — perguntas, ideias e contribuições são bem-vindas.

## Licença

[Apache-2.0](LICENSE).
