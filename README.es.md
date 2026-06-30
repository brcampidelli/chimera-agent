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

Chimera fusiona **múltiples LLMs por solicitud** — un pipeline **panel → juez → sintetizador**
inspirado en OpenRouter Fusion — en lugar de depender de un único modelo de frontera, y
**se mejora con el tiempo** (memoria → skills → modelo), resistiendo la
*degradación por evolución continua* que limita a los agentes actuales.

> **Estado:** desarrollo temprano (0.1.x). El plan de construcción completo (M0–M7) está
> implementado — Tiers 1–4 + el motor de Fusión + autoevolución + un kernel de gobernanza — más
> una **capa de interfaces** (chat, TUI, gateway HTTP), **evolución de modelo opt-in** y una
> **capa de funcionalidades** (Vision, Modo Entregable, Pets, …).
> 224 tests (+ integración en vivo opt-in) · `mypy --strict` limpio · `ruff` limpio.

---

## Por qué Chimera

Los frameworks existentes son fuertes en un solo eje: Hermes/OpenClaw evolucionan skills pero
usan un único modelo; CrewAI/LangGraph orquestan bien pero no aprenden; TrustClaw/NemoClaw/ZeroClaw
aportan seguridad/sandbox pero no evolucionan. **Chimera combina las cuatro:**

- 🧬 **Fusión como razonamiento** — el motor panel→juez→sintetizador es el núcleo de razonamiento, no un complemento. La mejora viene del propio paso de *síntesis*, no solo de la diversidad de modelos.
- 🪜 **Cuatro niveles de capacidad en una progresión** — herramientas aumentadas → autónomo de tarea única → equipos multiagente → ecosistema autoevolutivo.
- ♻️ **Autoevolución multinivel** que ataca explícitamente la degradación por evolución continua (estado externalizado, contexto resistente al drift, verify-or-revert, buffer de experiencia).
- 🛡️ **Un kernel de gobernanza que también se mejora** — allow/warn/block/review, con una superficie de automodificación validada estáticamente.

## Funcionalidades

**Razonamiento y autonomía**
- **Motor LLM-Fusion** — panel agnóstico de proveedor de modelos de frontera + abiertos, un juez que revela consensos/contradicciones/puntos ciegos, y un sintetizador; un **enrutador consciente del costo** fusiona solo cuando compensa (los turnos de herramienta usan un único modelo).
- **Autonomía Tier-2** — planificar → ejecutar → revisión del Manager → **verify-or-revert** (snapshot/restauración del workspace + un verificador por comando), con un buffer de experiencia estilo git.
- **Equipos multiagente** — especialización por roles, crews secuenciales y supervisor, consolidación de mensajes MOC, memoria compartida, revisión paralela.

**Autoevolución y gobernanza**
- **Autoevolución** — un Memory Manager (dedupe ADD/UPDATE/DELETE/NOOP), un evolucionador de skills que *escribe y prueba sus propias skills* (proponer → probar → conservar/descartar), crons autoaprendidos y un **benchmark de evolución continua** (más un stress test EvoClaw naive-vs-guarded) que mide la degradación.
- **Evolución de modelo opt-in** — `solve` recolecta trayectorias; `evolve` las cura en datasets SFT/DPO y emite una receta LoRA ejecutable. El entrenamiento queda **externo y opt-in** — nunca automático.
- **Gobernanza y seguridad** — un kernel de confianza que se mejora (allow/warn/block/review), un validador estático para la superficie de automodificación, un registro de auditoría append-only y herramientas gobernadas.

**Proveedores**
- **Cualquier modelo, una interfaz** — agnóstico de proveedor vía LiteLLM (100+ modelos mediante slugs `provider/model`); claves first-class para OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted y resiliente** — endpoints personalizados para **Ollama/vLLM** (`CHIMERA_API_BASE`), **cadenas de fallback** entre modelos, **credential pools** con rotación round-robin de claves, y cambio de modelo **`/model`** en vivo en `chat`/`tui`.

**Interfaces e integraciones**
- **CLI-first, más interfaces** — un REPL `chat`, una **TUI** a pantalla completa (Textual) y un **gateway de mensajería** HTTP con una conversación (y memoria) por chat.
- **Integraciones** — un cliente **MCP** (stdio) first-class + un importador **OpenAPI/REST → tool**, para añadir cualquier plataforma o API.
- **Crons y proactividad** — tareas programadas asignadas por humanos y autoaprendidas.
- **Migración** — importa config, skills y **memoria de largo plazo** de Hermes Agent / OpenClaw (la memoria se *fusiona*, nunca se sobrescribe).

**Extras integrados**
- **Vision** (pegar imagen), **Modo Entregable** (produce artefactos pulidos y autocontenidos) y un **Pet** compañero — más slots de credenciales pre-set para búsqueda web, generación de imágenes, TTS/voz y más (`chimera features` muestra qué está listo y qué necesita cada uno).

## Inicio rápido

Requiere Python **3.11+** (3.12+ recomendado) y [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env        # define al menos una clave de proveedor (OpenRouter recomendado)
uv run chimera doctor       # verifica tu entorno
```

## Comandos

```bash
chimera doctor / models / features    # estado, configuración, capacidades opcionales
chimera chat                          # asistente multironda interactivo (tu mano derecha)
chimera tui                           # app de terminal a pantalla completa (Textual)
chimera serve                         # servidor HTTP del gateway de mensajería (sesiones por chat)
chimera run "PROMPT" --image pic.png   # Tier-1 de un disparo (con visión vía --image)
chimera deliver "un plan de lanzamiento" -o plan.md   # Modo Entregable: produce un artefacto pulido
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: panel -> juez -> sintetizador
chimera agent "TAREA" --fuse --guard    # bucle ReAct de herramientas (llamadas gobernadas)
chimera solve "TAREA" --verify "pytest -q"   # Tier-2 autónomo: planificar -> verify-or-revert
chimera crew "TAREA" --mode supervisor  # crew multiagente Tier-3
chimera meta "un agente para X"          # meta-agente Tier-4: diseña un agente especializado
chimera memory add "un hecho duradero"    # memoria de largo plazo curada (deduplicada)
chimera cron add NOMBRE "0 9 * * *" "ejecutar informe"   # programa una tarea
chimera cron learn                     # propone crons a partir de tareas recurrentes (desactivado)
chimera bench                          # benchmark de evolución continua
chimera guard "rm -rf /"               # vista previa de un veredicto de gobernanza
chimera migrate hermes ~/.hermes --apply   # importa config + skills + fusiona memoria
chimera evolve status / recipe             # evolución de modelo opt-in: datos SFT/DPO + receta LoRA
chimera pet new --name Chimi               # adopta un compañero virtual (los stats decaen con el tiempo)
```

Consulta la **[Guía de Uso](docs/usage.md)** para instalación, configuración y cada comando con ejemplos copy-paste.

## Arquitectura

```
chimera/
  core/          bucle del agente (ReAct) + autonomía Tier-2 (plan, verify-or-revert, supervisor)
  fusion/        panel -> juez -> sintetizador + enrutador consciente del costo
  memory/        working / episodic / semantic / persona + Memory Manager
  skills/        biblioteca integrada + recuperación de skill-context
  evolution/     evolucionador de skills aprendidas, buffer de experiencia
  governance/    kernel de confianza (allow/warn/block/review), validador estático, auditoría, tools gobernadas
  orchestration/ roles, crews secuenciales y supervisor, comms MOC
  ecosystem/     meta-agente, gobernanza del ritmo de cambio, recolección de trayectorias, evolución de modelo
  tools/         herramientas nativas (archivos, shell, http)
  integrations/  cliente MCP (stdio) + importador OpenAPI->tool
  scheduler/     crons (asignados + autoaprendidos) + motor de SOP
  migration/     importa de Hermes/OpenClaw (config, skills, merge de memoria)
  providers/     gateway de LLM (LiteLLM) — cadenas de fallback, credential pools, endpoints personalizados
  interface/     ChatSession conversacional (compartida por chat, TUI, gateway)
  tui/           app Textual a pantalla completa
  server/        gateway de mensajería + transporte HTTP (sesiones por chat)
  eval/          evolución continua + stress test EvoClaw + escenarios diarios
  cli/           el comando `chimera` (CLI-first)
```

Consulta [docs/architecture.md](docs/architecture.md) para el diseño completo y la investigación en que se basa.

## Roadmap

| Hito | Estado |
|---|---|
| M0 — Fundamentos (gateway, config, CLI) | ✅ |
| M1 — Tier 1 + tools/skills/integraciones/crons/migración | ✅ |
| M2 — Motor LLM-Fusion + enrutador consciente del costo | ✅ |
| M3 — Tier 2 autónomo (verify-or-revert) | ✅ |
| M4 — Autoevolución (memoria, skills, crons aprendidos, benchmark) | ✅ |
| M5 — Kernel de gobernanza | ✅ |
| M6 — Equipos multiagente Tier 3 | ✅ |
| M7 — Ecosistema autoevolutivo Tier 4 | ✅ |
| M8 — Interfaces (chat/TUI/gateway), stress-test EvoClaw, evolución de modelo opt-in | ✅ |
| Capa de proveedores — endpoints self-hosted, cadenas de fallback, credential pools, `/model` | ✅ |
| Funcionalidades — Vision, Modo Entregable, Pets + slots de capacidad pre-set | ✅ |

Tras M7, el agente fue endurecido contra modelos reales de proveedor (probado en vivo: Fusión,
`solve` Tier-2, la suite de escenarios diarios, el gateway HTTP, el importador OpenAPI y el cliente
MCP stdio). Siguiente: validación de evolución continua más profunda a escala, más integraciones
de proveedor (logins OAuth, tuning de credential pools) y un backend opcional de durabilidad LangGraph.

## Desarrollo

```bash
uv run ruff check .      # lint
uv run mypy chimera      # type-check (strict)
uv run pytest -q         # tests
```

Consulta [CONTRIBUTING.md](CONTRIBUTING.md) y [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
Problemas de seguridad: consulta [SECURITY.md](SECURITY.md).

## Comunidad

Únete a la conversación en **[Discord](https://discord.gg/ACvBbrmguV)** — preguntas, ideas y contribuciones son bienvenidas.

## Licencia

[Apache-2.0](LICENSE).
