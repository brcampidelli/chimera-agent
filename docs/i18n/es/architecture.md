---
source_sha256: 1f1c80dd0c5b6b4b6bade6bfbe0cf3d94969fa6ec1e9918d2e186fad3f1a2cd4
---

# Chimera — Arquitectura

Este documento relaciona el código con el diseño y con la investigación en la que se basa. Para el
"por qué", consulta [VISION.md](https://github.com/brcampidelli/chimera-agent/blob/main/VISION.md).

## El núcleo de razonamiento: LLM-Fusion

`chimera/fusion/`

El motor de fusión ejecuta una tarea a través de un **panel** de modelos, hace que un **juez**
produzca un análisis estructurado (consenso / contradicciones / cobertura parcial / hallazgos
únicos / puntos ciegos), y luego un **sintetizador** escribe la respuesta final fundamentada en
ese análisis (`FusionEngine`). Implementa el protocolo `SupportsComplete`, por lo que es un
backend de razonamiento intercambiable en cualquier lugar donde se espere un modelo — incluso
dentro del bucle del agente.

Un **enrutador consciente del costo** (`RoutedBackend` + `RoutingPolicy`) mantiene la fusión
selectiva: los turnos de llamada a herramientas van a un solo modelo (la fusión no hace
tool-calling), y solo se fusionan los turnos de razonamiento profundo / de alto riesgo. Inspirado
en OpenRouter Fusion (la mejora viene del paso de *síntesis*, no solo de la diversidad de
modelos) y en AURORA-AI (presupuesto adaptativo entre modelos heterogéneos).

## El bucle del agente y la autonomía de Tier-2

`chimera/core/`

- `Agent` — un bucle mínimo ReAct / de llamada a herramientas con una **transcripción explícita**
  (el estado vive fuera del modelo). Depende solo de `SupportsComplete` + un `ToolRegistry`.
- `AutonomousAgent` — Tier-2: ensamblar el contexto de **Spine** con alcance de propiedad →
  **planificar** → snapshot → ejecutar → **revisión del Manager** (generate-vs-verify) →
  **verify-or-revert** → reintentar con retroalimentación, registrando cada intento en el buffer
  de experiencia.
- `WorkspaceGuard` — snapshot/restauración de archivos de texto, el mecanismo detrás de
  verify-or-revert.
- `CommandVerifier` — "evidencia ejecutable" (exit 0 == éxito).

### Enfrentando la degradación de la evolución continua

El problema abierto (según *Agentic Software*, `2606.05608`): el rendimiento cae de >80% en
tareas aisladas a ~38% en evolución continua — contexto de largo horizonte + propagación de
errores. Las contramedidas de Chimera, cada una fundamentada en la literatura:

| Contramedida | Dónde | Base |
|---|---|---|
| Externalizar el estado (transcripción/workspace, no el contexto del LLM) | `core`, `WorkspaceGuard` | HORIZON `2606.28279` |
| Contexto con alcance de propiedad (Spine) | `core/spine.py` | Spec Growth Engine `2606.27045` |
| Supervisión generate-vs-verify | `core/supervisor.py` | AdvancedShelLM `2606.27990` |
| Verify-or-revert | `core/autonomous.py` | autoresearch / AutoMegaKernel `2606.09682` |
| Buffer de experiencia (fallos como negativos) | `evolution/experience.py` | HORIZON `2606.28279` |
| Consolidación de mensajes en equipos | `orchestration/comms.py` | MOC `2606.02359` |
| Benchmark de evolución continua | `eval/continuous.py` | Enunciado del problema EvoClaw |

## Memoria y autoevolución

`chimera/memory/`, `chimera/evolution/`

- **Memory Manager** — elementos jerárquicos (working / episodic / semantic / persona) con
  `ADD / UPDATE / DELETE / NOOP` (`remember`) y deduplicación por `merge` (Memory-R1,
  `2606.14502`).
- **Skill evolver** — `SkillEvolver` propone un `LearnedSkill` reutilizable a partir de un éxito,
  lo prueba, y lo conserva solo si pasa (proponer → probar → conservar/descartar). Las skills
  aprendidas son **plantillas de prompt, no código ejecutable** — seguras de generar de forma
  autónoma antes de una automodificación a nivel de código. El refinamiento mejora una plantilla
  a partir de sus fallos (VIBEMed `2606.15504`).
- **Crons autoaprendidos** — `CronLearner` detecta tareas recurrentes y propone crons
  (`created_by=agent`, **deshabilitados** hasta la aprobación humana).
- **Benchmark de evolución continua** — ejecuta una cadena de tareas a través de un solver y
  reporta la degradación (tasa de éxito general, primera mitad vs. segunda mitad, racha más
  larga).

## Gobernanza y seguridad

`chimera/governance/`

Un kernel de confianza autosuperador (AgentTrust v2, `2606.08539`):

- `TrustKernel.evaluate(action)` → **allow / warn / block / review**. Un `RuleSet` léxico
  maneja de forma determinista las amenazas de firma fija; un **juez semántico** opcional maneja
  la intención; las reglas destiladas lo abaratan con el tiempo. Invariante: **nunca bloquear de
  forma dura una acción benigna**.
- `SkillValidator` / `ScheduleValidator` — la **superficie de edición restringida y verificable
  estáticamente** para la automodificación (AutoMegaKernel `2606.09682`): las propuestas
  inseguras se rechazan antes de que lleguen a ejecutarse.
- `AuditLog` — JSONL de solo anexado con las decisiones y los cambios de evolución.
- `GovernedTool` / `govern_registry` — envuelve cualquier herramienta para que su ejecución quede
  controlada; se compone con el bucle del agente existente sin modificarlo (`chimera ... --guard`).

### La capa de taint (contención de inyección de prompts)

Superpuesta al kernel — heurística, honesta, y nunca un límite duro (eso es el sandbox):

- `TaintLedger` + `LedgeredTool` (`ledger.py`, `ledger_tool.py`) — un libro de capacidades por
  ejecución. Un fetch mancha (taint) su contenido; una escritura/ejecución que consume contenido
  manchado **escala a revisión** (`assess_action`). El contenido no confiable obtenido se
  devuelve **cercado como datos** y con los tokens de control de la plantilla de chat eliminados
  (`sanitize.py`), y los artefactos duraderos de una ejecución manchada conservan una
  procedencia `tainted` para que el veneno no pueda blanquearse hacia una memoria/skill "limpia".
- `AggregateMonitor` (`aggregate_monitor.py`) — un monitor un nivel por encima: dados los eventos
  de capacidad de cada subagente, detecta **flujos divididos** que un monitor por agente no
  puede ver (el agente A obtiene contenido no confiable, el agente B lo ejecuta o **lo
  exfiltra**).
- `check_drift` (`drift.py`) — una `Spec` de requisitos ejecutables (`defines`/`contains`/`absent`/
  `command`) que sirve tanto de verdad de referencia para `solve --verify` como de autoridad del
  orquestador de proyecto sobre "hecho" (más abajo). Las comprobaciones negativas fallan de forma
  cerrada en archivos que no pueden escanear.
- `QuarantineTool` + lista blanca adaptativa (`quarantine.py`, `allowlist.py`) — un lector
  en cuarentena estilo dual-LLM/CaMeL y una lista blanca de herramientas adaptativa al taint que
  se estrecha una vez que una ejecución queda manchada.

## Equipos multiagente (Tier 3)

`chimera/orchestration/`

- `Role` + `RoleAgent` — especialización de roles (estilo CrewAI).
- `SequentialCrew` — roles en orden, cada uno ve las salidas previas **consolidadas** y puede
  escribir en la memoria compartida.
- `SupervisorCrew` — los workers abordan la tarea en paralelo, las salidas se consolidan, y un
  supervisor sintetiza (estilo CAPRA `parallel_review`, `2606.18976`).
- `consolidate` — la fusión de mensajes MOC mantiene el contexto del equipo ligero
  (`2606.02359`).

## Ecosistema autoevolutivo (Tier 4)

`chimera/ecosystem/`

- `MetaAgent` — diseña/construye/evalúa agentes especializados (agentes construyendo agentes).
  Dos salvaguardas del Meta-Agent Challenge (`2606.04455`): **aislamiento de herramientas** (las
  herramientas de un agente diseñado se filtran a una lista permitida) y **separación de
  pruebas ocultas** (pasar lo visible + fallar lo oculto ⇒ se sospecha reward-hacking, no se
  acredita como éxito).
- `ChangeQueue` — gobierna el *ritmo* del cambio (cola de fusión FIFO + límites por lote), no la
  cantidad de agentes ("Govern the Repository", `2606.28235`).
- `TrajectoryCollector` — registra (prompt, respuesta, resultado) y exporta datasets **SFT /
  DPO**. El ajuste fino real es **opcional y externo** — Chimera recolecta, no entrena.

## Economía de costos y la jerarquía de delegación

`chimera/orchestration/` (hierarchy, cascade, budget, receipts, envelope_verify)

La delegación solo compensa cuando es más barata que hacer el trabajo en línea, y la afirmación
se **mide, no se asume**:

- `HierarchicalOrchestrator` — descomponer → despachar workers con presupuesto → verificar cada
  resultado → sintetizar. Las tareas con forma de lectura se delegan en fan-out; una subtarea
  trivialmente pequeña la responde en línea el modelo principal de confianza.
- `CascadeBackend` — débil → gate → medio → gate → fusión, escalando solo cuando la respuesta de
  un nivel falla una comprobación de aceptación barata. El **route log** registra cada salto,
  así que el costo es la **suma sobre los saltos intentados**, no solo el aceptado — las
  escalaciones se pagan.
- `TokenBudget` / `BudgetedBackend` / `EffortPolicy` — un techo de tokens estricto impuesto en el
  backend, por worker.
- `EnvelopeVerifier` — esquema → criterios de aceptación → **spot check** probabilístico (evalúa
  la fidelidad de un resumen frente al artefacto en bruto); una repregunta disparada por un fallo
  puntual se reauditá.
- **Recibos de delegación** (`receipts.py`) — cada delegación registra sus tokens/costo medidos
  **y el contrafactual en línea en la misma fila**, valorado a la tarifa propia de cada modelo
  (modelo desconocido → `None`, nunca inventado). La sobrecarga propia de
  descomponer/sintetizar del orquestador también se mide, así que `summarize_delegations`
  (`chimera delegations`) reporta un ahorro neto **auditable**, y `cascade-bench` reporta la
  **cola** de costo (p50/p95/p99), no solo la media.

## El volante de autoevolución

`chimera/evolution/`

El "entrenamiento" que nunca toca los pesos — señalizado por fitness, sin gradiente, y
reversible:

- `EvolutionContext` — el ensamblaje compartido (experience, trajectories, memory,
  auto-evolver, skill cards, playbook) que hace del aprendizaje una propiedad del *stack* del
  agente, no solo del comando `solve`.
- Skill cards + refinamiento **GEPA**, **playbook** ACE, y una `SkillLifecyclePolicy` que
  promueve/degrada una skill según sus estadísticas **medidas** de uso/éxito (una skill nueva
  nace `provisional`).
- El **diff-gate** — un "éxito hueco" (el verificador pasó pero el diff del workspace está
  vacío) no acuña una skill ni una memoria; el volante solo aprende del trabajo que realmente
  ocurrió.
- El **transfer-gate** (`eval/transfer.py`) — un artefacto ajustado se promueve solo si también
  se sostiene en un holdout, protegiendo contra la transferencia negativa.
  `maturity.Scorecard.weakest()` es el objetivo: el bucle apunta a la capacidad más débil. Las
  regresiones hacen rollback automático solo ante una caída **estadísticamente significativa**
  (un IC, nunca un solo punto).

Cada cambio de un valor por defecto está protegido detrás de un A/B pareado **pre-registrado**
(`bench/`), publicado gane o pierda — sin re-tirar los dados en busca de significancia.

## Autonomía de proyecto (de principio a fin)

`chimera/orchestration/project.py`

`ProjectOrchestrator` ejecuta un proyecto completo contra una `Spec`: grafo de tareas (un DAG
tipo Kanban con `depends_on`) → cada tarjeta lista se resuelve (con el contexto de evolución
anterior) → **se acepta contra la Spec** vía `check_drift` (la única autoridad sobre "hecho") →
los requisitos no cumplidos generan las siguientes tarjetas, en bucle hasta que la Spec queda
alineada o un presupuesto / máximo de iteraciones / punto de control humano lo detiene. Los pasos
riesgosos (`risk: high` — deploy / migración / borrado) **se pausan para aprobación humana**; la
ejecución es duradera y reanudable.

## Transversal

- **Providers** (`providers/`) — una única puerta de enlace agnóstica de proveedor sobre
  LiteLLM; las claves pueden vivir en `.env` y se exportan al entorno para que LiteLLM las vea.
- **Tools** (`tools/`) — primitivas nativas; los metadatos de las herramientas son atributos de
  instancia para que las herramientas generadas dinámicamente (OpenAPI/MCP) funcionen.
- **Integrations** (`integrations/`) — cliente MCP (extra opcional `mcp`) + importador
  OpenAPI→tool + registro de conectores.
- **Scheduler** (`scheduler/`) — crons + SOPs de eventos; el tiempo se inyecta para pruebas
  deterministas.
- **Migration** (`migration/`) — importa config + skills + **fusiona** la memoria de largo plazo
  de Hermes / OpenClaw, deduplicada y no destructiva.

## Filosofía de pruebas

Cada subsistema está probado con pruebas unitarias usando **backends falsos** — deterministas,
sin red, sin claves. Los comandos que realmente llaman a un LLM tienen pruebas de humo para su
ruta de fallo sin clave. El quality gate (`ruff` + `mypy --strict` + `pytest`) corre en CI sobre
Python 3.11 y 3.12.
