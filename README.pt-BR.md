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

O Chimera funde **múltiplas LLMs por requisição** — um pipeline **painel → juiz → sintetizador**,
inspirado no OpenRouter Fusion — em vez de depender de um único modelo de fronteira, e
**aprimora a si mesmo com o tempo** (memória → skills → modelo), resistindo à *degradação de
evolução contínua* que limita os agentes atuais.

> **Status:** alpha inicial. Os 8 marcos do plano de construção (M0–M7) estão implementados:
> Tiers 1–4 + o motor de Fusão + autoevolução + um kernel de governança.
> 158 testes · `mypy --strict` limpo · `ruff` limpo.

---

## Por que o Chimera

Cada framework existente é forte em **um eixo**: Hermes/OpenClaw evoluem skills mas rodam um
único modelo; CrewAI/LangGraph orquestram bem mas não aprendem; TrustClaw/NemoClaw/ZeroClaw
trazem segurança/sandbox mas não evoluem. **O Chimera combina os quatro:**

- 🧬 **Fusão como raciocínio** — o motor painel→juiz→sintetizador é o núcleo de raciocínio, não um complemento. O ganho vem da *síntese* em si, não só da diversidade de modelos.
- 🪜 **Quatro tiers de capacidade numa única progressão** — ferramentas aumentadas → autônomo de tarefa única → equipes multiagentes → ecossistema autoevolutivo.
- ♻️ **Autoevolução multinível** que ataca explicitamente a degradação de evolução contínua (estado externalizado, contexto resistente a drift, verify-or-revert, buffer de experiência).
- 🛡️ **Um kernel de governança que também se aprimora** — allow/warn/block/review, com uma superfície de automodificação validada estaticamente.

## Recursos

- **Motor de Fusão de LLMs** — painel provider-agnostic de modelos de fronteira + abertos, um juiz que expõe consensos/contradições/blind-spots, e um sintetizador; um **roteador custo-consciente** funde só quando compensa (turnos com ferramentas ficam em modelo único).
- **Autonomia Tier-2** — planejar → executar → revisão do Manager → **verify-or-revert** (snapshot/restore do workspace + verificador por comando), com um buffer de experiência estilo git.
- **Autoevolução** — um Memory Manager (dedup ADD/UPDATE/DELETE/NOOP), um evolutor de skills que *escreve e testa suas próprias skills* (propor → testar → manter/descartar), crons auto-aprendidos e um **benchmark de evolução contínua** que mede a degradação.
- **Equipes multiagentes** — especialização por papéis, crews sequencial e supervisor, consolidação de mensagens MOC, memória compartilhada, revisão paralela.
- **Governança & segurança** — um kernel de confiança autoevolutivo, um validador estático para a superfície de automodificação, um log de auditoria append-only e tools governadas.
- **Integrações** — cliente **MCP** de primeira classe + um importador **OpenAPI/REST → tool**, para adicionar qualquer plataforma ou API.
- **Crons & proatividade** — tarefas agendadas atribuídas por humanos e auto-aprendidas.
- **Migração** — importe config, skills e **memória de longo prazo** do Hermes Agent / OpenClaw (a memória é *mesclada*, nunca sobrescrita).
- **CLI-first** — tudo funciona pelo terminal; provider-agnostic via LiteLLM/OpenRouter.

## Início rápido

Requer Python **3.11+** (3.12+ recomendado) e [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # defina ao menos uma chave de provider (OpenRouter recomendado)
uv run chimera doctor       # verifique seu ambiente
```

## Comandos

```bash
chimera doctor / models               # status & configuração
chimera run "PROMPT"                   # completamento Tier-1 de um disparo
chimera fuse "PROMPT" --show-panel     # Fusão de LLMs: painel -> juiz -> sintetizador
chimera agent "TASK" --fuse --guard    # loop do agente ReAct (tool calls governadas)
chimera solve "TASK" --verify "pytest -q"   # Tier-2 autônomo: planejar -> verify-or-revert
chimera crew "TASK" --mode supervisor  # crew multiagente Tier-3
chimera meta "an agent for X"          # meta-agente Tier-4: projeta um agente especializado
chimera memory add "um fato durável"   # memória de longo prazo curada (deduplicada)
chimera cron add NAME "0 9 * * *" "run report"   # agenda uma tarefa
chimera cron learn                     # propõe crons a partir de tarefas recorrentes (desabilitados)
chimera bench                          # benchmark de evolução contínua
chimera guard "rm -rf /"               # pré-visualiza um veredito de governança
chimera migrate hermes ~/.hermes --apply   # importa config + skills + mescla memória
```

## Arquitetura

```
chimera/
  core/          loop do agente (ReAct) + autonomia Tier-2 (plano, verify-or-revert, supervisor)
  fusion/        painel -> juiz -> sintetizador + roteador custo-consciente
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        biblioteca embutida + recuperação de skill-context
  evolution/     evolutor de skills aprendidas, buffer de experiência
  governance/    kernel de confiança (allow/warn/block/review), validador estático, audit, tools governadas
  orchestration/ papéis, crews sequencial & supervisor, comms MOC
  ecosystem/     meta-agente, governança de tempo de mudança, coleta de trajetórias
  tools/         tools nativas (arquivos, shell, http)
  integrations/  cliente MCP + importador OpenAPI->tool
  scheduler/     crons (atribuídos + auto-aprendidos) + engine de SOP
  migration/     import do Hermes/OpenClaw (config, skills, merge de memória)
  providers/     adapters de LLM (LiteLLM / OpenRouter)
  eval/          benchmark de evolução contínua, tarefas demo
  cli/           o comando `chimera` (CLI-first)
```

Veja [docs/architecture.md](docs/architecture.md) para o design completo e a pesquisa em que se baseia.

## Roadmap

| Marco | Status |
|---|---|
| M0 — Fundações (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + tools/skills/integrações/crons/migração | ✅ |
| M2 — Motor de Fusão de LLMs + roteador custo-consciente | ✅ |
| M3 — Tier 2 autônomo (verify-or-revert) | ✅ |
| M4 — Autoevolução (memória, skills, crons aprendidos, benchmark) | ✅ |
| M5 — Kernel de governança | ✅ |
| M6 — Equipes multiagentes Tier 3 | ✅ |
| M7 — Ecossistema autoevolutivo Tier 4 | ✅ |

A seguir: validação com modelos reais em escala, uma suíte de evolução contínua expandida e um backend opcional de durabilidade com LangGraph.

## Desenvolvimento

```bash
uv run ruff check .      # lint
uv run mypy chimera      # checagem de tipos (strict)
uv run pytest -q         # testes
```

Veja [CONTRIBUTING.md](CONTRIBUTING.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Problemas de segurança: veja [SECURITY.md](SECURITY.md).

## Comunidade

Participe da conversa no **[Discord](https://discord.gg/ACvBbrmguV)** — perguntas, ideias e contribuições são bem-vindas.

## Licença

[Apache-2.0](LICENSE).
