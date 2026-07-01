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
> implementado — Tiers 1–4 + el motor de Fusión + autoevolución multinivel + un kernel de
> gobernanza — más un **bucle de aprendizaje conductual cerrado**, una **capa operativa**
> (Kanban + worker lanes, crew SDLC, un DSL declarativo de bucles), **aislamiento de
> ejecución** (sandbox Docker + git worktrees) y las **técnicas de los papers** en que se
> basa (HORIZON, VIBEMed, Spec Growth, AgentTrust v2, AutoMegaKernel, Meta-Agent, MOC).
> 332 tests (+ integración en vivo opt-in) · `mypy --strict` limpio · `ruff` limpio.

---

## Por qué Chimera

Los frameworks existentes son fuertes en un solo eje: Hermes/OpenClaw evolucionan skills pero
usan un único modelo; CrewAI/LangGraph orquestan bien pero no aprenden; TrustClaw/NemoClaw/ZeroClaw
aportan seguridad/sandbox pero no evolucionan. **Chimera combina las cuatro:**

- 🧬 **Fusión como razonamiento** — el motor panel→juez→sintetizador es el núcleo de razonamiento, no un complemento. La mejora viene del propio paso de *síntesis*, no solo de la diversidad de modelos.
- 🪜 **Cuatro niveles de capacidad en una progresión** — herramientas aumentadas → autónomo de tarea única → equipos multiagente → ecosistema autoevolutivo.
- ♻️ **Un bucle de autoevolución multinivel cerrado** que ataca explícitamente la degradación por evolución continua (estado externalizado, contexto resistente al drift, verify-or-revert, un buffer de experiencia realimentado en la planificación).
- 🛡️ **Un kernel de gobernanza que también se mejora** — allow/warn/block/review, con una superficie de automodificación validada estáticamente y precedente guardado.

## Funcionalidades

**Razonamiento y autonomía**
- **Motor LLM-Fusion** — panel agnóstico de proveedor de modelos de frontera + abiertos, un juez que revela consensos/contradicciones/puntos ciegos, y un sintetizador; un **enrutador consciente del costo** fusiona solo cuando compensa (los turnos de herramienta usan un único modelo).
- **Autonomía Tier-2** — planificar → ejecutar → revisión del Manager (opcionalmente vía una **rúbrica en cascada**, `solve --rubric`) → **verify-or-revert** (snapshot/restauración del workspace + un verificador por comando), con **aislamiento en git worktree** (`solve --isolate`) — los cambios solo se aplican si se verifican.
- **Crew de ciclo de vida SDLC** (`chimera lifecycle`) — pipeline pre-armado **plan → build → test → review** con verify-or-revert en la etapa de test.
- **Equipos multiagente** — especialización por roles, crews secuenciales y supervisor, consolidación de mensajes MOC, memoria compartida, revisión paralela. Los roles de crew pueden ser **workers que usan herramientas** (con su propio loop + herramientas), no solo personas de un solo disparo, y cualquier agente puede **`spawn_subagent`** (`solve --subagents`) para delegar una subtarea a un subagente aislado y con alcance de herramientas acotado que devuelve solo su resultado (sin recursión, limitado por allowlist). **`IsolatedCrew`** (`chimera crew-isolated`) va más allá — workers que usan herramientas dividen una tarea, cada uno editando en su **propio git worktree** en paralelo, con merge-back consciente de conflictos y una puerta `--verify` opcional por worker (un worker cuyo test falla es rechazado y sus ediciones descartadas).
- **Aislamiento paralelo** (`chimera solve-batch`) — resuelve muchas tareas a la vez, cada una en su **propio git worktree**; las ediciones sin conflicto se fusionan de vuelta y los archivos que dos workers tocaron a la vez se marcan como conflictos, no se sobrescriben. Un worker que se cae falla su unidad, no el lote (`run_in_processes` añade una frontera de proceso/RPC para aislar fallos).
- **Context Explorer** (`chimera explore`, `solve --explorer`) — un subagente aislado estilo FastContext que localiza código mediante su propia búsqueda de solo lectura con `grep`/`glob`/lectura y devuelve únicamente un bloque de evidencia compacto `file:line`, manteniendo limpio el contexto del agente principal. Corre sobre cualquier modelo (idealmente barato).

**Autoevolución y gobernanza**
- **Bucle conductual cerrado** — los fallos pasados alimentan el planner (lecciones), los éxitos verificados auto-escriben memoria, y las tareas recurrentes auto-evolucionan una skill validada y smoke-testeada (propuesta a través del panel de fusión y conservada por transferibilidad entre modelos cuando la fusión está activa) — todo gateado por verify-or-revert; un intento fallido se ubica en su primer paso defectuoso en el reintento. Más un benchmark de evolución continua y un stress test EvoClaw naive-vs-guarded.
- **Memoria jerárquica** — working / episodic / semantic / persona **+ una capa graph** (`memory graph`) que recupera hechos por entidad; un backend de texto completo **SQLite/FTS5** opcional (`CHIMERA_MEMORY_BACKEND=sqlite`); un **perfil de usuario entre sesiones** (hechos de persona aplicados en cada turno); **consolidación por LLM** (`memory consolidate`) que fusiona hechos casi duplicados; y **nudges** que sugieren guardar las preferencias que enuncias en el chat.
- **Evolución de modelo opt-in** — `solve` recolecta trayectorias; `evolve` cura datasets SFT/DPO y emite una receta LoRA ejecutable, y **`evolve tune`** auto-optimiza la spec del agente (meta-búsqueda, conservada bajo no-regresión) contra los escenarios diarios. El entrenamiento queda externo/opt-in.
- **Kernel de gobernanza** — allow/warn/block/review (reglas léxicas + juez semántico opcional, con destilación de reglas y un **almacén de precedentes guardado**), un validador estático de la superficie de automodificación, un registro de auditoría append-only, herramientas gobernadas, un **modelo de cambio de 4 actores**, y un **drift gate spec↔código** (`chimera drift`).

**Proveedores**
- **Cualquier modelo, una interfaz** — agnóstico de proveedor vía LiteLLM (100+ modelos mediante slugs `provider/model`); claves first-class para OpenRouter/OpenAI/Anthropic/Gemini/DeepSeek.
- **Self-hosted y resiliente** — endpoints personalizados para **Ollama/vLLM** (`CHIMERA_API_BASE`), **cadenas de fallback**, **credential pools** con rotación round-robin, cambio de modelo **`/model`** en vivo, y **prompt caching** (`CHIMERA_CACHE`) para turnos de razonamiento repetidos.

**Orquestación, interfaces e integraciones**
- **Kanban + worker lanes** (`chimera kanban`) — un tablero (backlog → doing → review → done) donde las tarjetas se despachan a una lane `solve` o `crew`; `kanban learn` convierte tareas recurrentes en tarjetas.
- **Loop Engineering** (`chimera workflow`) — escribe un bucle autónomo en YAML (pasos que `usan` la stack, con condiciones `when` y bucles `repeat`/`until`).
- **Interfaces** — un REPL `chat`, una **TUI** a pantalla completa (Textual) y un **gateway de mensajería** (HTTP, o **Discord / Telegram / Slack / Signal nativo** vía `serve --discord|--telegram|--slack|--signal`) con una conversación (y memoria) por chat; el agente también puede **enviar** mensajes con la herramienta `send_message`. **WhatsApp** funciona bidireccionalmente vía un webhook de la Cloud API (`POST /whatsapp`).
- **Sandbox de ejecución** — ejecuta el shell localmente o en un contenedor **Docker** aislado (`CHIMERA_SANDBOX=docker`).
- **Integraciones** — un cliente **MCP** (stdio) first-class + un importador **OpenAPI/REST → tool**; **crons + triggers webhook** (`serve` ejecuta una tarea al recibir un `POST /webhook/<hook>` entrante — desatendido); **migración** de config/skills/memoria de largo plazo de Hermes Agent / OpenClaw.

**Extras integrados**
- **Herramientas de referencia** — con baterías incluidas: `execute_code` siempre activo (Python en sandbox), `code_interpreter` (sesión con estado), `arxiv_search`; con puerta de config `web_search`, `generate_image` (OpenAI), `text_to_speech` (ElevenLabs), `send_email`/`read_email` (SMTP/IMAP), `calendar_events` (`.ics`); y `youtube_transcript` (extra opt-in). Servicios REST arbitrarios siguen conectándose vía el importador OpenAPI→tool.
- **Vision** (pegar imagen), **Modo Entregable** (artefactos pulidos) y un **Pet** compañero — mira todas las capacidades opcionales con `chimera features`.

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
chimera serve [--discord|--telegram|--slack]  # gateway de mensajería: HTTP, o bot de plataforma nativo
chimera run "PROMPT" --image pic.png   # Tier-1 de un disparo (con visión vía --image)
chimera deliver "un plan" -o plan.md   # Modo Entregable: produce un artefacto pulido
chimera fuse "PROMPT" --show-panel     # LLM-Fusion: panel -> juez -> sintetizador
chimera solve "TAREA" --verify "pytest -q" --rubric --isolate   # Tier-2: verify-or-revert (+ revisión por rúbrica en cascada), aislado en git worktree
chimera lifecycle "TAREA" --verify "..."   # crew SDLC: plan -> build -> test -> review
chimera workflow flow.yaml             # ejecuta un bucle declarativo (Loop Engineering)
chimera crew "TAREA" --mode supervisor  # crew multiagente Tier-3
chimera meta "un agente para X"          # meta-agente Tier-4: diseña un agente especializado
chimera kanban add/board/run/learn     # tablero de tareas con worker lanes (solve/crew)
chimera drift spec.yaml                # drift gate spec<->código (sale 1 en drift)
chimera memory add / graph             # memoria de largo plazo curada + grafo entidad-relación
chimera cron add / learn               # tareas programadas (asignadas + autoaprendidas, confirmadas)
chimera bench                          # benchmark de evolución continua
chimera migrate hermes ~/.hermes --apply   # importa config + skills + fusiona memoria
chimera evolve status / tune / recipe   # evolución opt-in: meta-búsqueda de spec (tune), datos SFT/DPO + receta LoRA
chimera pet new --name Chimi           # adopta un compañero virtual
```

Consulta la **[Guía de Uso](docs/usage.md)** para instalación, configuración y cada comando con ejemplos copy-paste.

## Arquitectura

```
chimera/
  core/          bucle del agente (ReAct) + autonomía Tier-2 (plan, verify-or-revert) + aislamiento git worktree
  fusion/        panel -> juez -> sintetizador + enrutador consciente del costo
  memory/        working / episodic / semantic / persona + capa graph + Memory Manager
  skills/        biblioteca integrada + recuperación de skill-context
  evolution/     evolucionador de skills, hook de auto-evolución, buffer de experiencia
  governance/    kernel de confianza (reglas + juez + precedente guardado), validador estático, drift gate, modelo de 4 actores, auditoría
  orchestration/ roles, crews secuenciales/supervisor, comms MOC, crew de ciclo de vida SDLC
  ecosystem/     meta-agente, gobernanza del ritmo de cambio, recolección de trayectorias, evolución de modelo
  kanban/        tablero de tareas + worker lanes (despacho a crews / solve)
  workflow/      DSL declarativo de bucles (Loop Engineering)
  tools/         herramientas nativas (archivos, shell, http)
  sandbox/       backends de ejecución (local / docker aislado)
  integrations/  cliente MCP (stdio) + importador OpenAPI->tool
  scheduler/     crons (asignados + autoaprendidos) + motor de SOP
  migration/     importa de Hermes/OpenClaw (config, skills, merge de memoria)
  providers/     gateway de LLM (LiteLLM) — fallback, credential pools, endpoints personalizados, prompt cache
  interface/     ChatSession conversacional (compartida por chat, TUI, gateway)
  tui/  server/   app Textual a pantalla completa · gateway de mensajería + transporte HTTP
  eval/          evolución continua + stress test EvoClaw + escenarios diarios
  cli/           el comando `chimera` (CLI-first)
```

Consulta [docs/architecture.md](docs/architecture.md) para el diseño completo y la investigación en que se basa.

## Roadmap

| Hito | Estado |
|---|---|
| M0–M7 — Tiers 1–4 + Fusión + autoevolución + gobernanza | ✅ |
| M8 — Interfaces (chat/TUI/gateway), stress-test EvoClaw, evolución de modelo opt-in | ✅ |
| Capa de proveedores — endpoints self-hosted, fallback, credential pools, `/model`, prompt cache | ✅ |
| Bucle conductual cerrado — experiencia→planner, auto-memoria, auto-skill (gobernado) | ✅ |
| Orquestación operativa — Kanban + worker lanes, crew SDLC, Loop DSL | ✅ |
| Aislamiento de ejecución — sandbox Docker + git worktrees | ✅ |
| Técnicas de los papers — HORIZON · VIBEMed · Spec Growth · AgentTrust v2 · AutoMegaKernel · Meta-Agent · MOC | ✅ |
| Técnicas de los papers (II) — MemGate · valor multifactor de memoria · Data Recipes · OpenClaw-Skill · SkillAdaptor · DailyReport · meta-búsqueda de spec OpenJarvis | ✅ |

Siguiente: validación de evolución continua más profunda a escala, logins OAuth de proveedor y un
backend opcional de durabilidad LangGraph. El entrenamiento de modelo (LoRA/DPO) queda externo/opt-in por diseño.

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
