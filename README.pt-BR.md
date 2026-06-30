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
> implementado — Tiers 1–4 + o motor de Fusão + autoevolução + um kernel de governança — mais
> uma **camada de interfaces** (chat, TUI, gateway HTTP), **evolução de modelo opt-in** e uma
> **camada de recursos** (Vision, Modo Entregável, Pets, …).
> 224 testes (+ integração ao vivo opt-in) · `mypy --strict` limpo · `ruff` limpo.

---

## Por que o Chimera

Os frameworks existentes são fortes em um único eixo: Hermes/OpenClaw evoluem skills mas rodam
um único modelo; CrewAI/LangGraph orquestram bem mas não aprendem; TrustClaw/NemoClaw/ZeroClaw
trazem segurança/sandbox mas não evoluem. **O Chimera combina os quatro:**

- 🧬 **Fusão como raciocínio** — o motor painel→juiz→sintetizador é o núcleo de raciocínio, não um add-on. O ganho vem do próprio passo de *síntese*, não só da diversidade de modelos.
- 🪜 **Quatro níveis de capacidade numa progressão** — ferramentas aumentadas → autônomo de tarefa única → equipes multiagentes → ecossistema autoevolutivo.
- ♻️ **Autoevolução multinível** que ataca explicitamente a degradação de evolução contínua (estado externalizado, contexto resistente a drift, verify-or-revert, buffer de experiência).
- 🛡️ **Um kernel de governança que também se aperfeiçoa** — allow/warn/block/review, com uma superfície de automodificação validada estaticamente.

## Recursos

**Raciocínio & autonomia**
- **Motor LLM-Fusion** — painel provider-agnostic de modelos de fronteira + abertos, um juiz que revela consensos/contradições/pontos cegos, e um sintetizador; um **roteador custo-consciente** funde só quando compensa (turnos de ferramenta seguem modelo único).
- **Autonomia Tier-2** — planejar → executar → revisão do Manager → **verify-or-revert** (snapshot/restauração do workspace + um verificador por comando), com um buffer de experiência estilo git.
- **Equipes multiagentes** — especialização por papéis, crews sequenciais e supervisor, consolidação de mensagens MOC, memória compartilhada, revisão paralela.

**Autoevolução & governança**
- **Autoevolução** — um Memory Manager (dedup ADD/UPDATE/DELETE/NOOP), um evolutor de skills que *escreve e testa as próprias skills* (propor → testar → manter/descartar), crons auto-aprendidos e um **benchmark de evolução contínua** (mais um stress test EvoClaw naive-vs-guarded) que mede a degradação.
- **Evolução de modelo opt-in** — o `solve` coleta trajetórias; o `evolve` as cura em datasets SFT/DPO e emite uma receita LoRA executável. O treino fica **externo e opt-in** — nunca automático.
- **Governança & segurança** — um kernel de confiança que se aperfeiçoa (allow/warn/block/review), um validador estático para a superfície de automodificação, um log de auditoria append-only e ferramentas governadas.

**Provedores**
- **Qualquer modelo, uma interface** — provider-agnostic via LiteLLM (100+ modelos por slugs `provider/model`); chaves first-class para OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted & resiliente** — endpoints custom para **Ollama/vLLM** (`CHIMERA_API_BASE`), **cadeias de fallback** entre modelos, **credential pools** com rotação round-robin de chaves, e troca de modelo **`/model`** ao vivo no `chat`/`tui`.

**Interfaces & integrações**
- **CLI-first, mais interfaces** — um REPL `chat`, uma **TUI** full-screen (Textual) e um **gateway de mensageria** HTTP com uma conversa (e memória) por chat.
- **Integrações** — um cliente **MCP** (stdio) first-class + um importador **OpenAPI/REST → tool**, para você adicionar qualquer plataforma ou API.
- **Crons & proatividade** — tarefas agendadas atribuídas por humanos e auto-aprendidas.
- **Migração** — importa config, skills e **memória de longo prazo** do Hermes Agent / OpenClaw (a memória é *mesclada*, nunca sobrescrita).

**Extras embutidos**
- **Vision** (colar imagem), **Modo Entregável** (produz artefatos polidos e autocontidos) e um **Pet** companheiro — mais slots de credencial pré-set para busca web, geração de imagem, TTS/voz e mais (`chimera features` mostra o que está pronto e o que cada um precisa).

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
chimera deliver "um plano de lançamento" -o plan.md   # Modo Entregável: produz um artefato polido
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: painel -> juiz -> sintetizador
chimera agent "TAREFA" --fuse --guard    # loop ReAct de ferramentas (chamadas governadas)
chimera solve "TAREFA" --verify "pytest -q"   # Tier-2 autônomo: planejar -> verify-or-revert
chimera crew "TAREFA" --mode supervisor  # crew multiagente Tier-3
chimera meta "um agente para X"          # meta-agente Tier-4: projeta um agente especializado
chimera memory add "um fato durável"    # memória de longo prazo curada (deduplicada)
chimera cron add NOME "0 9 * * *" "rodar relatório"   # agenda uma tarefa
chimera cron learn                     # propõe crons a partir de tarefas recorrentes (desativado)
chimera bench                          # benchmark de evolução contínua
chimera guard "rm -rf /"               # prévia de um veredito de governança
chimera migrate hermes ~/.hermes --apply   # importa config + skills + mescla memória
chimera evolve status / recipe             # evolução de modelo opt-in: dados SFT/DPO + receita LoRA
chimera pet new --name Chimi               # adote um companheiro virtual (stats decaem com o tempo)
```

Veja o **[Guia de Uso](docs/usage.md)** para instalação, configuração e cada comando com exemplos copy-paste.

## Arquitetura

```
chimera/
  core/          loop do agente (ReAct) + autonomia Tier-2 (plano, verify-or-revert, supervisor)
  fusion/        painel -> juiz -> sintetizador + roteador custo-consciente
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        biblioteca embutida + recuperação de skill-context
  evolution/     evolutor de skills aprendidas, buffer de experiência
  governance/    kernel de confiança (allow/warn/block/review), validador estático, auditoria, tools governadas
  orchestration/ papéis, crews sequenciais e supervisor, comms MOC
  ecosystem/     meta-agente, governança de tempo de mudança, coleta de trajetórias, evolução de modelo
  tools/         ferramentas nativas (arquivos, shell, http)
  integrations/  cliente MCP (stdio) + importador OpenAPI->tool
  scheduler/     crons (atribuídos + auto-aprendidos) + engine de SOP
  migration/     importa do Hermes/OpenClaw (config, skills, merge de memória)
  providers/     gateway de LLM (LiteLLM) — cadeias de fallback, credential pools, endpoints custom
  interface/     ChatSession conversacional (compartilhada por chat, TUI, gateway)
  tui/           app Textual full-screen
  server/        gateway de mensageria + transporte HTTP (sessões por chat)
  eval/          evolução contínua + stress test EvoClaw + cenários diários
  cli/           o comando `chimera` (CLI-first)
```

Veja [docs/architecture.md](docs/architecture.md) para o design completo e a pesquisa em que se baseia.

## Roadmap

| Marco | Status |
|---|---|
| M0 — Fundações (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + tools/skills/integrações/crons/migração | ✅ |
| M2 — Motor LLM-Fusion + roteador custo-consciente | ✅ |
| M3 — Tier 2 autônomo (verify-or-revert) | ✅ |
| M4 — Autoevolução (memória, skills, crons aprendidos, benchmark) | ✅ |
| M5 — Kernel de governança | ✅ |
| M6 — Equipes multiagentes Tier 3 | ✅ |
| M7 — Ecossistema autoevolutivo Tier 4 | ✅ |
| M8 — Interfaces (chat/TUI/gateway), stress-test EvoClaw, evolução de modelo opt-in | ✅ |
| Camada de provedores — endpoints self-hosted, cadeias de fallback, credential pools, `/model` | ✅ |
| Recursos — Vision, Modo Entregável, Pets + slots de capacidade pré-set | ✅ |

Pós-M7, o agente foi endurecido contra modelos reais de provedor (testado ao vivo: Fusão,
`solve` Tier-2, a suíte de cenários diários, o gateway HTTP, o importador OpenAPI e o cliente
MCP stdio). A seguir: validação de evolução contínua mais profunda em escala, mais integrações
de provedor (logins OAuth, tuning de credential pools) e um backend opcional de durabilidade LangGraph.

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
