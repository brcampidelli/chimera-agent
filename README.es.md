<div align="center">

<img src="assets/logo-wide.png" alt="Logo de Chimera" width="460" />

# Chimera

**Un agente de IA open-source y autoevolutivo cuyo núcleo de razonamiento es un motor de Fusión de LLMs.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/brcampidelli/chimera-agent/actions/workflows/ci.yml)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-2a6db2.svg)](https://mypy-lang.org/)
[![Linted with Ruff](https://img.shields.io/badge/lint-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Discord](https://img.shields.io/badge/Discord-unirse-5865F2.svg?logo=discord&logoColor=white)](https://discord.gg/ACvBbrmguV)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)

<sub><a href="README.md">English</a> · <a href="README.pt-BR.md">Português</a> · <b>Español</b> · <a href="README.de.md">Deutsch</a> · <a href="README.fr.md">Français</a> · <a href="README.zh-CN.md">中文</a> · <a href="README.ja.md">日本語</a></sub>

</div>

Chimera fusiona **varios LLMs por petición** — un pipeline **panel → juez → sintetizador**,
inspirado en OpenRouter Fusion — en lugar de depender de un único modelo de frontera, y
**se mejora a sí mismo con el tiempo** (memoria → skills → modelo), resistiendo la *degradación
por evolución continua* que limita a los agentes actuales.

> **Estado:** alpha temprana. Los 8 hitos del plan de construcción (M0–M7) están implementados:
> Tiers 1–4 + el motor de Fusión + autoevolución + un kernel de gobernanza.
> 158 pruebas · `mypy --strict` limpio · `ruff` limpio.

---

## Por qué Chimera

Cada framework existente es fuerte en **un eje**: Hermes/OpenClaw evolucionan skills pero usan un
solo modelo; CrewAI/LangGraph orquestan bien pero no aprenden; TrustClaw/NemoClaw/ZeroClaw
aportan seguridad/sandbox pero no evolucionan. **Chimera combina los cuatro:**

- 🧬 **Fusión como razonamiento** — el motor panel→juez→sintetizador es el núcleo de razonamiento, no un añadido. La mejora viene de la *síntesis* en sí, no solo de la diversidad de modelos.
- 🪜 **Cuatro tiers de capacidad en una sola progresión** — herramientas aumentadas → autónomo de tarea única → equipos multiagente → ecosistema autoevolutivo.
- ♻️ **Autoevolución multinivel** que ataca explícitamente la degradación por evolución continua (estado externalizado, contexto resistente a drift, verify-or-revert, búfer de experiencia).
- 🛡️ **Un kernel de gobernanza que también se mejora** — allow/warn/block/review, con una superficie de automodificación validada estáticamente.

## Características

- **Motor de Fusión de LLMs** — panel agnóstico de proveedor de modelos de frontera + abiertos, un juez que expone consensos/contradicciones/puntos ciegos, y un sintetizador; un **enrutador consciente del coste** fusiona solo cuando compensa (los turnos con herramientas usan un único modelo).
- **Autonomía Tier-2** — planificar → ejecutar → revisión del Manager → **verify-or-revert** (snapshot/restore del workspace + verificador por comando), con un búfer de experiencia estilo git.
- **Autoevolución** — un Memory Manager (dedup ADD/UPDATE/DELETE/NOOP), un evolucionador de skills que *escribe y prueba sus propias skills* (proponer → probar → conservar/descartar), crons autoaprendidos y un **benchmark de evolución continua** que mide la degradación.
- **Equipos multiagente** — especialización por roles, crews secuencial y supervisor, consolidación de mensajes MOC, memoria compartida, revisión en paralelo.
- **Gobernanza y seguridad** — un kernel de confianza autoevolutivo, un validador estático para la superficie de automodificación, un registro de auditoría append-only y herramientas gobernadas.
- **Integraciones** — cliente **MCP** de primera clase + un importador **OpenAPI/REST → tool**, para añadir cualquier plataforma o API.
- **Crons y proactividad** — tareas programadas asignadas por humanos y autoaprendidas.
- **Migración** — importa config, skills y **memoria a largo plazo** de Hermes Agent / OpenClaw (la memoria se *fusiona*, nunca se sobrescribe).
- **CLI-first** — todo funciona desde la terminal; agnóstico de proveedor vía LiteLLM/OpenRouter.

## Inicio rápido

Requiere Python **3.11+** (3.12+ recomendado) y [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # define al menos una clave de proveedor (OpenRouter recomendado)
uv run chimera doctor       # verifica tu entorno
```

## Comandos

```bash
chimera doctor / models               # estado y configuración
chimera run "PROMPT"                   # completado Tier-1 de un solo paso
chimera fuse "PROMPT" --show-panel     # Fusión de LLMs: panel -> juez -> sintetizador
chimera agent "TASK" --fuse --guard    # bucle del agente ReAct (tool calls gobernadas)
chimera solve "TASK" --verify "pytest -q"   # Tier-2 autónomo: planificar -> verify-or-revert
chimera crew "TASK" --mode supervisor  # crew multiagente Tier-3
chimera meta "an agent for X"          # meta-agente Tier-4: diseña un agente especializado
chimera memory add "un hecho duradero" # memoria a largo plazo curada (deduplicada)
chimera cron add NAME "0 9 * * *" "run report"   # programa una tarea
chimera cron learn                     # propone crons a partir de tareas recurrentes (deshabilitados)
chimera bench                          # benchmark de evolución continua
chimera guard "rm -rf /"               # previsualiza un veredicto de gobernanza
chimera migrate hermes ~/.hermes --apply   # importa config + skills + fusiona memoria
```

## Arquitectura

```
chimera/
  core/          bucle del agente (ReAct) + autonomía Tier-2 (plan, verify-or-revert, supervisor)
  fusion/        panel -> juez -> sintetizador + enrutador consciente del coste
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        biblioteca integrada + recuperación de skill-context
  evolution/     evolucionador de skills aprendidas, búfer de experiencia
  governance/    kernel de confianza (allow/warn/block/review), validador estático, audit, tools gobernadas
  orchestration/ roles, crews secuencial y supervisor, comms MOC
  ecosystem/     meta-agente, gobernanza del ritmo de cambios, recolección de trayectorias
  tools/         tools nativas (archivos, shell, http)
  integrations/  cliente MCP + importador OpenAPI->tool
  scheduler/     crons (asignados + autoaprendidos) + motor de SOP
  migration/     importación desde Hermes/OpenClaw (config, skills, fusión de memoria)
  providers/     adaptadores de LLM (LiteLLM / OpenRouter)
  eval/          benchmark de evolución continua, tareas demo
  cli/           el comando `chimera` (CLI-first)
```

Consulta [docs/architecture.md](docs/architecture.md) para el diseño completo y la investigación en que se basa.

## Roadmap

| Hito | Estado |
|---|---|
| M0 — Fundamentos (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + tools/skills/integraciones/crons/migración | ✅ |
| M2 — Motor de Fusión de LLMs + enrutador consciente del coste | ✅ |
| M3 — Tier 2 autónomo (verify-or-revert) | ✅ |
| M4 — Autoevolución (memoria, skills, crons aprendidos, benchmark) | ✅ |
| M5 — Kernel de gobernanza | ✅ |
| M6 — Equipos multiagente Tier 3 | ✅ |
| M7 — Ecosistema autoevolutivo Tier 4 | ✅ |

Próximo: validación con modelos reales a escala, una suite de evolución continua ampliada y un backend opcional de durabilidad con LangGraph.

## Desarrollo

```bash
uv run ruff check .      # lint
uv run mypy chimera      # verificación de tipos (strict)
uv run pytest -q         # pruebas
```

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) y [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Problemas de seguridad: consulta [SECURITY.md](SECURITY.md).

## Comunidad

Únete a la conversación en **[Discord](https://discord.gg/ACvBbrmguV)** — preguntas, ideas y contribuciones son bienvenidas.

## Licencia

[Apache-2.0](LICENSE).
