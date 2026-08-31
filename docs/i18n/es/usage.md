---
source_sha256: f4c7b57b8bec8e9aa96ead432d65d90113b2b11c9e24c15f7f703c2c14520786
---

# Chimera — Guía de uso

Chimera es un agente autoevolutivo, primero-CLI, con un núcleo de razonamiento LLM-Fusion.
Esta guía cubre la instalación, la configuración, y cada comando con ejemplos.

> ¿Nuevo en el proyecto? Lee primero la [visión general de la arquitectura](architecture.md).

---

## Instalación

Chimera usa [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brcampidelli/chimera-agent
cd chimera-agent
uv sync --extra dev      # install runtime + dev deps
uv run chimera --help    # verify the CLI
```

Cada comando de abajo se ejecuta como `uv run chimera <command>` (o simplemente `chimera …`
una vez que el virtualenv del proyecto esté en tu PATH).

---

## Configuración

Chimera es agnóstico de proveedor vía [LiteLLM](https://docs.litellm.ai/). Pon tus claves y
elecciones de modelo en un `.env` local (está ignorado por git — nunca lo hagas commit):

```dotenv
# At least one provider key. OpenRouter unlocks 100+ models behind one key.
OPENROUTER_API_KEY=sk-or-...
# OPENAI_API_KEY=...
# ANTHROPIC_API_KEY=...

# Tier-1/2 default model (single, cheap, must support tool-calling for Tier-2)
CHIMERA_DEFAULT_MODEL=openrouter/deepseek/deepseek-chat-v3.1

# LLM-Fusion: a diverse panel -> judge -> synthesizer
CHIMERA_FUSION_PANEL=openrouter/deepseek/deepseek-chat-v3.1,openrouter/openai/gpt-4o-mini,openrouter/meta-llama/llama-3.3-70b-instruct
CHIMERA_FUSION_JUDGE=openrouter/deepseek/deepseek-chat-v3.1
CHIMERA_FUSION_SYNTHESIZER=openrouter/openai/gpt-4o-mini
```

Otros ajustes: `CHIMERA_HOME` (directorio de estado, por defecto `.chimera`),
`CHIMERA_LOG_LEVEL` (`INFO` / `DEBUG`), `CHIMERA_CACHE` (`on`/`off`, por defecto off —
cachea completions idénticas sin herramientas para saltar llamadas API repetidas), y
`CHIMERA_AUTO_FUSE` (`on`/`off`, por defecto off — fusiona automáticamente turnos profundos o
**sensibles a error** en `solve`/`crew` sin un `--fuse` explícito; el enrutador consciente del
costo sigue manteniendo los turnos baratos/con herramientas en un solo modelo). El enrutador
reconoce prompts de respuesta exacta (aritmética, conteo, operaciones con dígitos) en los
idiomas principales del proyecto (en/pt/es/de/fr/zh/ja), así que un paso corto crítico recibe la
protección de la fusión incluso cuando es demasiado corto para activar la barrera de longitud.

**Proveedores, fallback y auto-alojado.** Cualquier slug `provider/model` de LiteLLM funciona
(`openai/…`, `anthropic/…`, `gemini/…`, `ollama/…`, `openrouter/…`, …). Para un servidor
auto-alojado / compatible con OpenAI (Ollama, vLLM) configura `CHIMERA_API_BASE` (p. ej.
`http://127.0.0.1:11434` con `CHIMERA_DEFAULT_MODEL=ollama/llama3`). Configura
`CHIMERA_FALLBACK_MODELS` (separado por comas) para conmutar a otro modelo si el primario
falla. En `chat`/`tui`, `/model <slug>` cambia el modelo a mitad de sesión.

**Pools de credenciales.** Dale a un proveedor varias claves con `CHIMERA_<PROVIDER>_KEYS`
(p. ej. `CHIMERA_OPENROUTER_KEYS=key1,key2,key3`). El gateway las rota en round-robin entre
llamadas (repartiendo carga / límites de tasa) y, dentro de una sola llamada, conmuta a la
siguiente clave si una falla. Un pool reemplaza la `*_API_KEY` única de ese proveedor.
*(Los logins OAuth/suscripción — Copilot, Claude Max, etc. — aún no están conectados; las
claves API y cualquier endpoint compatible con LiteLLM sí lo están.)*

Verifica que todo esté conectado:

```bash
uv run chimera doctor    # shows version, default model, configured providers
uv run chimera models    # shows the fusion panel / judge / synthesizer
uv run chimera features  # optional capabilities + what each needs (key/dep)
```

**Funcionalidades opcionales.** Vision, Deliverable Mode y el Pet vienen incorporados. El
resto (búsqueda web, búsqueda en X, generación de imágenes, TTS/voz, Spotify, navegador) son
ranuras preconfiguradas: rellena la credencial correspondiente en `.env` (o instala la
dependencia) y la capacidad se activa. `chimera features` es la lista de verificación en vivo.
La herramienta `web_search` (Tavily) se autorregistra en el momento en que se configura
`TAVILY_API_KEY` — y es la plantilla para agregar las demás (o usa el cliente MCP /
importador OpenAPI->tool).

> **Modelos gratuitos vs. de pago.** Los modelos `:free` de OpenRouter no cuestan nada, pero
> tienen límite de tasa aguas arriba — bien para un `run` rápido, inestables para comandos de
> múltiples llamadas como `fuse`/`solve`. Para uso real, un modelo de pago barato (p. ej.
> `deepseek/deepseek-chat-v3.1`, fracciones de centavo por llamada) es mucho más confiable.

---

## Comandos

### Estado — `version` · `doctor` · `models`

```bash
uv run chimera version
uv run chimera doctor
uv run chimera models
```

### `chat` — asistente interactivo de múltiples turnos (tu mano derecha)

Un REPL interactivo con memoria de conversación y uso de herramientas — el vehículo diario.
Recuerda memoria de largo plazo relevante y encadena la conversación entre turnos.

```bash
uv run chimera chat                 # start chatting; /exit to quit, /reset to clear context
uv run chimera chat --fuse          # fuse deep-reasoning turns
uv run chimera chat --no-memory     # don't recall long-term memory
```

El mismo núcleo conversacional impulsa la TUI y (próximamente) el gateway de mensajería.

### `tui` — aplicación de terminal a pantalla completa

Una interfaz Textual a pantalla completa sobre el mismo núcleo conversacional. Dos paneles: un
**registro de conversación** que renderiza las respuestas como Markdown (el código en bloques
tiene resaltado de sintaxis), con los tokens del modelo **transmitiéndose en vivo** a medida que
llegan; y un **panel de actividad** que muestra qué hizo el agente en este turno — las
herramientas que llamó, el conteo de tokens y el costo, y cuántos hechos de memoria se
recuperaron. Mismas flags que `chat`.

```bash
uv run chimera tui
uv run chimera tui --no-stream        # answers render at the end instead of streaming
uv run chimera tui --fuse --no-memory # fusion routing (no token stream — the panel says so)
```

Comandos: `/model <slug>` · `/reset` (limpiar contexto) · `/clear` (limpiar pantalla) ·
`/stream` (alternar tokens en vivo) · `/help` · `/exit`. Teclas: `Ctrl+R` reiniciar ·
`Ctrl+L` limpiar · `Ctrl+P` paleta de comandos · `PgUp`/`PgDn` desplazar · `Ctrl+C` salir. Los
slash commands se autocompletan mientras escribes.

Notas de honestidad: el streaming de tokens es solo la ruta de un solo modelo — bajo `--fuse`
(un turno panel→juez→sintetizador) no hay tokens incrementales, así que el panel muestra un
estado "sintetizando" en lugar de un cursor falso. El costo muestra "no disponible" cuando el
precio de lista del modelo es desconocido (nunca se adivina). No hay un indicador de
verify/revert aquí: verify-or-revert corre en `solve`/`project`, no en chat. Si Textual no está
instalado, `tui` recae en el REPL simple de `chat`.

### `serve` — gateway de mensajería (HTTP o Discord)

Expone al agente con una conversación (y su memoria) **por chat**. El núcleo de enrutamiento es
agnóstico de transporte; los adaptadores se conectan.

```bash
uv run chimera serve --port 8765          # HTTP transport
# GET  /health           -> {"status":"ok","active_chats":N}
# POST /chat  {"text":"...", "chat_id":"alice"}  -> {"reply":"...","chat_id":"alice"}
```

Cada `chat_id` mantiene su propio contexto, así que distintos usuarios/hilos no se mezclan.

**Operación desatendida (webhooks).** Registra un trabajo que se dispara ante un POST HTTP
entrante, así Chimera corre sin que nadie escriba — un push de GitHub, un evento de Stripe, un
ping de cron-as-a-service:

```bash
chimera cron add "on push" gh-push "Summarize the pushed commits" --webhook
chimera serve                              # then POST to the hook:
# curl -X POST localhost:8765/webhook/gh-push -d '{"ref":"refs/heads/main"}'
```

El cuerpo del POST se entrega a la tarea del trabajo como contexto, y cada trabajo registrado
para ese hook se ejecuta. `GET /health` y `POST /chat` siguen funcionando en paralelo.

**Discord nativo.** Ejecuta Chimera como un bot de Discord — cada canal es una sesión, y el
agente también puede enviar mensajes vía la herramienta `send_message`:

```bash
uv sync --extra messaging                 # installs discord.py
export CHIMERA_DISCORD_BOT_TOKEN=...       # bot token (Message Content intent enabled)
uv run chimera serve --discord
```

Crea el bot en <https://discord.com/developers>, habilita el intent de **Message Content**, e
invítalo a tu servidor. Responde en cualquier canal que pueda ver (filtrado para ignorar sus
propios mensajes y los de otros bots). El token se lee del entorno — nunca está fijo en el
código.

**Telegram nativo.** Mismo patrón de adaptador, y **no necesita ninguna dependencia extra** (la
API del Bot de Telegram es HTTP plano):

```bash
export CHIMERA_TELEGRAM_BOT_TOKEN=...      # from @BotFather
uv run chimera serve --telegram
```

**Slack nativo.** Recibe vía Socket Mode (necesita el extra `messaging`) y envía vía la Web API.
Habilita Socket Mode en tu app de Slack para obtener un token a nivel de app:

```bash
uv sync --extra messaging
export CHIMERA_SLACK_BOT_TOKEN=xoxb-...     # bot token
export CHIMERA_SLACK_APP_TOKEN=xapp-...     # app-level token (Socket Mode)
uv run chimera serve --slack
```

**WhatsApp (envío).** WhatsApp funciona por *push* (los mensajes llegan a un webhook de Meta que
tú alojas), así que a diferencia de los demás no hay conexión que abrir. Configura las
credenciales de Cloud API y el agente puede **enviar** mensajes de WhatsApp vía la herramienta
`send_message` en cualquier modo de `serve`:

```bash
export CHIMERA_WHATSAPP_ACCESS_TOKEN=...
export CHIMERA_WHATSAPP_PHONE_NUMBER_ID=...
# in a chat: send_message(platform="whatsapp", chat_id="<E.164 number>", text="done ✅")
```

**WhatsApp bidireccional.** Apunta el webhook de tu app de Meta a `https://<your-host>/whatsapp`
y configura `CHIMERA_WHATSAPP_VERIFY_TOKEN` (cualquier string que elijas, que coincida con la
configuración de la app). `chimera serve` entonces verifica la suscripción (`GET /whatsapp`) y
enruta los mensajes entrantes (`POST /whatsapp`) a través del gateway, respondiendo vía la
Cloud API. WhatsApp aún necesita una URL pública para el webhook — esa es la única parte fuera
de Chimera.

**Signal nativo (bidireccional).** Signal no tiene API oficial, así que Chimera habla con un
puente [`signal-cli-rest-api`](https://github.com/bbernhard/signal-cli-rest-api) que tú
ejecutas (Docker) y vinculas a tu número — HTTP plano, sin dependencia de Python:

```bash
docker run -d -p 8080:8080 -v signal-cli:/home/.local/share/signal-cli bbernhard/signal-cli-rest-api
export CHIMERA_SIGNAL_API_URL=http://localhost:8080
export CHIMERA_SIGNAL_NUMBER=+15550000000     # this bot's registered number
uv run chimera serve --signal
```

### `run` — Tier-1, completion de un solo disparo

Una sola llamada al modelo, sin herramientas, sin fusión. La ruta más barata.

```bash
uv run chimera run "In one sentence, what is an AI agent?"
uv run chimera run "Summarize this error" --model openrouter/openai/gpt-4o-mini
```

**Visión / pegado de imagen.** Adjunta imágenes con `--image` (una ruta o URL, repetible) —
necesita un modelo con capacidad de visión:

```bash
uv run chimera run "What's in this chart?" --image chart.png -m openrouter/google/gemini-2.5-flash
```

### `deliver` — Deliverable Mode (producir un artefacto)

Donde `run`/`chat` responden de forma conversacional, `deliver` produce un documento completo y
autocontenido (informe, plan, especificación, README...) y lo escribe en un archivo.

```bash
uv run chimera deliver "A one-page launch plan for a URL shortener" --out plan.md
uv run chimera deliver "An HTML status page" --format html -o status.html --fuse
```

### `agent` — el bucle crudo de llamada a herramientas ReAct

Pensamiento → Acción (herramienta) → Observación, hasta una respuesta final. Las herramientas
están acotadas al workspace.

```bash
uv run chimera agent "Create a file hello.txt containing 'Hello Chimera'" -w ./scratch
```

### `fuse` — LLM-Fusion (el diferenciador)

Ejecuta un *panel* de modelos, un *juez* analiza sus respuestas (consenso / contradicciones /
puntos ciegos), y un *sintetizador* escribe la respuesta final. Usa `--show-panel` para ver la
traza completa.

```bash
uv run chimera fuse "Name three concrete ways to prevent SQL injection in Python."
uv run chimera fuse "Compare REST vs gRPC for a mobile backend." --show-panel
```

La fusión cuesta ~2-3× una sola llamada, así que resérvala para razonamiento difícil. `fuse`
también imprime el costo de tokens por etapa (panel / juez / síntesis) para que puedas ver
adónde van realmente los tokens de una ejecución.

**Fusión selectiva (ON por defecto, ahorra tokens).** El motor sondea los primeros
`CHIMERA_FUSION_PROBE_K` modelos del panel (2 por defecto) y, cuando sus respuestas concuerdan
de cerca, omite el resto del panel *y* al juez — sintetizando directamente desde las respuestas
que concuerdan. La comprobación de concordancia es una comparación de texto local barata (sin
llamada extra al modelo), así que un turno *en desacuerdo* escala al pipeline completo y cuesta
exactamente lo mismo que la fusión completa, mientras que un turno *en concordancia* es más
barato. Ajusta el umbral con `CHIMERA_FUSION_AGREEMENT` (0–1, por defecto 0.8), o configura
`CHIMERA_FUSION_MODE=full` (o pasa `--full`) para correr siempre el panel + juez completos.

Por qué es el valor por defecto: en 3 ejecuciones de `chimera fusion-bench --tasks hard` (un
panel de 3 modelos de pago) recortó los tokens **~20–28%** y fue correcto en **cada** turno que
realmente cortocircuitó (16/16). La precisión general osciló entre 0 y −8.3pp entre
ejecuciones, pero esa varianza cae por completo en el balde *escalado* — donde selectivo corre
el pipeline idéntico al completo — así que es no-determinismo del modelo, no un costo del
early-stopping. Corre el bench en tu propia carga de trabajo para ver la compensación para tu
panel y tareas:

```bash
uv run chimera fuse "What is 12 * 12?" --show-panel   # likely early-stops
uv run chimera fusion-bench --tasks hard              # full vs selective, tokens + accuracy
```

> **Elige modelos de panel confiables.** La fusión solo vale la pena si cada miembro del panel
> realmente responde. Evita los slugs de modelo `:free` de OpenRouter en `CHIMERA_FUSION_PANEL`
> — tienen límite de tasa (HTTP 429) bajo carga real, y el panel se reduce silenciosamente al
> modelo de pago que quede. Un trío barato y confiable: `openrouter/deepseek/deepseek-chat`,
> `openrouter/openai/gpt-4o-mini`, `openrouter/meta-llama/llama-3.3-70b-instruct`.

### Skill cards (tarjetas de razonamiento TRS, experimental)

El agente destila lo que aprende en **tarjetas de razonamiento** — los cinco campos Trigger /
Do / Avoid / Check / Risk (más palabras clave de recuperación) — tanto de éxitos (una tarjeta de
*patrón*) como de fallos recurrentes (una tarjeta consultiva de *antipatrón*). Cuando
`CHIMERA_SKILL_CARDS=on`, `solve` recupera las top-k tarjetas relevantes (BM25 sobre
nombre + descripción + triggers) y las inyecta en el contexto de razonamiento del worker, así
el agente reutiliza lo que funcionó y evita modos de fallo conocidos. Esto cierra el ciclo —
antes, las skills aprendidas se almacenaban y nunca se volvían a leer.

Desactivado por defecto: inyectar tarjetas agrega tokens de prompt, y los ahorros de *tokens*
de TRS vienen de acortar trazas de razonamiento largas, así que en tareas de respuesta corta la
ventaja es precisión, no costo. Esto no es hipotético — en la suite de respuesta corta `hard`
(deepseek-v3.1 de pago), `skillcard-bench` midió que las tarjetas costaban **+290% de tokens**
y **−8pp de precisión** frente a no usar tarjetas: con un modelo cerca del techo y sin traza
larga que acortar, las tarjetas genéricas son puro overhead que puede distraer. Habilita las
tarjetas para cargas de trabajo de **razonamiento largo** (matemáticas/programación con trazas
extensas) donde la matemática de tokens se invierte, y siempre mide tu propia compensación
primero con una comprobación de verdad de referencia:

```bash
uv run chimera skillcard-bench --tasks hard          # demo cards vs no cards
uv run chimera skillcard-bench --use-store --tasks hard   # bench your own learned cards
export CHIMERA_SKILL_CARDS=on CHIMERA_SKILL_CARDS_K=3      # enable, once it earns its place
```

El bench reporta la precisión con vs. sin tarjetas, la delta de tokens, la tasa de acierto de
tarjetas, y la precisión desglosada por acierto/fallo, con un veredicto PASS cuando la precisión
con tarjetas se mantiene dentro de 1pp de la baseline sin tarjetas.

### Esquemas de herramientas compactos (experimental)

Los esquemas de herramientas — especialmente los importados de servidores MCP o
especificaciones OpenAPI — llevan ruido de anotación (ejemplos, títulos, valores por defecto,
prosa de parámetros de varias frases, cuerpos de solicitud anidados) que se reenvía al modelo en
**cada** paso ReAct. Con `CHIMERA_COMPACT_SCHEMAS=on`, ese ruido se elimina y las descripciones
de parámetros se recortan en el momento de anunciarse, **sin** tocar nada que afecte una llamada
(el nombre y la descripción de la función, y el `type` / `properties` / `required` / `enum` de
cada esquema, se preservan). Los esquemas canónicos permanecen intactos — solo se reduce la
copia enviada al modelo.

El ahorro es mayor en conjuntos de herramientas MCP/OpenAPI verbosos y se acumula en cada paso;
las herramientas nativas ya son concisas, así que su reducción es pequeña. Mide tu propio
conjunto de herramientas primero (sin llamadas al modelo — solo cuenta tokens):

```bash
uv run chimera schema-bench --demo                   # synthetic verbose tools, to see the effect
uv run chimera schema-bench --openapi ./openapi.json # your real spec's tools
```

Desactivado por defecto. Como la compactación solo elimina ruido de anotación (nunca
estructura), el único riesgo es que el modelo tenga un poco menos de prosa para elegir una
herramienta — así que se mantiene conservador, y deberías confirmar el comportamiento de
llamada a herramientas en tu carga de trabajo antes de habilitarlo.

### `solve` — Tier-2 autónomo (plan + verify-or-revert)

Planifica la tarea, ejecuta con el bucle del agente, y luego **verifica con un comando
ejecutable**. Si la verificación falla, revierte el workspace y reintenta con
retroalimentación. El verificador (código de salida 0 = éxito) es la verdad de referencia.

```bash
uv run chimera solve \
  "Create solution.py with add(a,b) and is_prime(n)." \
  --workspace ./work \
  --verify "python -c \"import solution; assert solution.is_prime(7)\""
```

Flags útiles:

| Flag | Significado |
|------|---------|
| `--verify "<cmd>"` | comando que debe salir con 0 (pruebas, un build, un linter) |
| `--workspace`, `-w` | dónde lee/escribe el agente (por defecto `.`) |
| `--max-attempts N` | presupuesto de verify-or-revert (por defecto 3) |
| `--max-steps N` | pasos de llamada a herramientas por intento (por defecto 8) |
| `--fuse` | produce el **plan** vía fusión (razonamiento profundo) |
| `--guard` | controla cada llamada a herramienta a través del kernel de gobernanza |
| `--no-plan` / `--no-manager` | omite la etapa de planificación / revisión |
| `--rubric` | el Manager juzga vía la **rúbrica en cascada** (seguimiento de instrucciones → veracidad → racionalidad) |
| `--no-remember` | no escribe automáticamente un hecho de memoria en caso de éxito |
| `--no-evolve-skills` | no propone automáticamente una skill aprendida cuando una tarea se repite |
| `--isolate` | corre en un git worktree desechable; los archivos cambiados se copian de vuelta solo si hay éxito |
| `--require-diff` | un intento que no cambió **ningún archivo** falla y se reintenta — para una tarea de código, una explicación no es una corrección |
| `--keep-workspace` | en caso de fallo, deja en disco las ediciones del último intento en lugar de revertirlas — para cuando un evaluador **externo** decide pass/fail |
| `--diff-feedback` | muestra a un intento fallido su propio diff revertido, enmarcado como un camino a no retomar |
| `--stagnation-fuzzy` | compara las firmas de fallo repetido de forma aproximada, así el pivote anti-estancamiento se dispara ante fallos de la misma causa cuya redacción difiere |

> **Sobre `--max-steps`.** El valor por defecto de 8 está ajustado para workspaces pequeños. En
> un **repositorio grande es la restricción vinculante**, no el modelo: la ejecución 1 de
> SWE-bench obtuvo un 0.0pp exacto con 8 pasos contra un checkout de 250 MB, y la misma
> configuración con **30 pasos** elevó la tasa de parches de la baseline del 47% al 74%
> ([`bench/swe_bench/RESULTS.md`](../bench/swe_bench/RESULTS.md)). Si el agente explora y
> luego termina sin editar, sube esto primero.

> **`--require-diff` y `--keep-workspace` son para calificación externa.** `solve` es
> verify-or-revert: cuando *él* es dueño de la decisión pass/fail, revertir un intento fallido
> es correcto. Cuando otra cosa es dueña de ella — un job de CI, un harness de benchmark, un
> humano revisando el diff — `--keep-workspace` evita que el trabajo del agente se revierta
> antes de que ese evaluador lo vea, y `--require-diff` evita que una explicación segura de sí
> misma se califique como un cambio completado. Ambos están **desactivados por defecto**.

**`solve` aprende entre ejecuciones.** Cada ejecución alimenta un ciclo conductual cerrado, todo
protegido por verify-or-revert así que solo el trabajo verificado tiene algún efecto: (1)
**lecciones** relevantes de intentos pasados (con preferencia a los fallos) se incorporan al
plan/prompt, y el **primer paso defectuoso** de un intento fallido se localiza y se alimenta al
reintento; (2) ante un éxito verificado se escribe un hecho de **memoria** deduplicado
(recuperado luego por `chat`/`crew`); y (3) cuando un patrón de tarea se repite (≥ 2 éxitos
previos), se propone una **skill** reutilizable — a través del panel de fusión y conservada por
**transferibilidad** entre modelos cuando `--fuse` está activo — y se conserva solo si pasa la
validación de gobernanza y una prueba de humo ejecutable.

### `crew` — Tier-3 multiagente

Un equipo de agentes con roles colabora en una tarea y un supervisor sintetiza la respuesta
final.

```bash
uv run chimera crew "Propose a minimal architecture for a URL shortener service."
```

### `lifecycle` — crew de SDLC (planificar → construir → probar → revisar)

Un pipeline de ciclo de vida de software preensamblado con **verify-or-revert** en la etapa de
pruebas: `plan` descompone la tarea, `build` la implementa, `test` ejecuta el verificador
(revirtiendo y reintentando el build en caso de fallo), y un revisor critica el resultado.

```bash
uv run chimera lifecycle "Add an add(a,b) function to solution.py" \
  --workspace ./scratch --verify "python -c \"import solution; assert solution.add(2,3)==5\""
```

Cada etapa se imprime con un ✓/✗; la ejecución es `success` solo si el verificador de la etapa
de pruebas pasó.

### `meta` — agentes construyendo agentes

Diseña el plano de un agente especializado (nombre, herramientas, prompt de rol) para una
tarea.

```bash
uv run chimera meta "an agent that triages GitHub issues and routes them to teams"
```

### `guard` — veredicto de gobernanza

Muestra la decisión del kernel de confianza (allow / warn / review / block) para una acción.

```bash
uv run chimera guard "rm -rf /"                       # BLOCK
uv run chimera guard "list the files in this folder"  # ALLOW
```

### `bench` — benchmark de evolución continua

Mide si el rendimiento *se mantiene* a lo largo de una cadena de tareas (la prueba
anti-degradación): tasa de aprobación general, primera mitad vs. segunda mitad, racha más
larga.

```bash
uv run chimera bench --limit 6           # single-shot task set
uv run chimera bench --chain --limit 6   # stateful chain (error propagation)
uv run chimera bench --fuse              # use fusion as the solver
```

El informe también lleva una bandera de degradación **estadísticamente honesta**: en lugar de
confiar en una resta cruda primera-menos-segunda-mitad (en una cadena corta, un vaivén de 0.2
suele ser ruido), `degraded_significant` es `1.0` solo cuando un intervalo de confianza de
Wilson sobre la caída excluye el cero, `-1.0` cuando la muestra es demasiado pequeña para
decirlo, y `0.0` en cualquier otro caso — más los límites `degradation_ci_low/high`. Por
separado, `CHIMERA_SKILL_ACCEPT_MODE=wilson` condiciona la decisión de aceptación de skill
entre modelos al límite de confianza *inferior* de la tasa de transferencia (así un pase
afortunado de 2 de 3 ya no cuenta); el valor por defecto `point` mantiene la tasa cruda, ya que
el límite de Wilson es estricto en paneles diminutos.

### `sandbox-bench` — calificación de estado + efectos secundarios

Los benches de texto califican la *respuesta* del modelo; este califica lo que el agente
**hizo**. Cada tarea corre en un directorio sandbox aislado, y el harness compara el estado
final de los archivos contra el objetivo (cualquier ruta permitida, estilo resultado) **y**
por separado cuenta los *efectos secundarios dañinos* — mutaciones fuera del conjunto
declarado de permitidos de la tarea. Así un agente que produce el resultado correcto mientras
destruye un archivo no relacionado queda atrapado, no calificado como un pase limpio.

```bash
uv run chimera sandbox-bench            # runs the demo stateful tasks (real models + file tools)
```

Reporta `pass_rate` y `side_effect_rate`. Provee la *metodología* (una `StatefulTask` con
`goal_check` + conjunto `allowed` de mutaciones), no una suite grande de tareas — crea tareas
para tus propias herramientas. Los calificadores de texto existentes siguen siendo correctos
para trabajo de puro preguntas y respuestas.

### `memory` — memoria de largo plazo curada

```bash
uv run chimera memory add "Alex prefers TypeScript strict and absolute imports"
uv run chimera memory search "imports"
uv run chimera memory list
uv run chimera memory graph                 # entity-relation graph from memory
uv run chimera memory graph --entity PassaPro   # one entity's relations
uv run chimera memory prune --max 50        # keep the N highest-value memories (multi-factor)
```

El recall pasa por una **puerta de admisión** (una frontera de confianza): un recuerdo
recuperado entra al prompt solo si es relevante *y* está libre de texto de override/inyección
(defensa contra jailbreak basada en memoria). `memory prune` olvida bajo un presupuesto según
un modelo de **valor** multifactor (recencia, especificidad, tipo, curación, confiabilidad) —
no una sola señal.

La **capa de grafo** extrae tripletas `(source, relation, target)` de tus memorias
(`PassaPro uses Supabase`, `Alex prefers TypeScript`), así los hechos se pueden recuperar por
entidad, no solo por palabra clave.

### `cron` — trabajos programados y SOPs de eventos

```bash
uv run chimera cron add daily-report "0 9 * * *" "generate the daily report"
uv run chimera cron list
```

### `kanban` — tablero de tareas con carriles de worker

Un tablero (`backlog → doing → review → done`) donde cada tarjeta nombra un *carril* que la
despacha al stack del agente: `solve` (Tier-2 autónomo, verify-or-revert) o `crew` (pipeline de
roles Tier-3). La vista operativa del bucle que el agente ya ejecuta.

```bash
uv run chimera kanban add "Fix the flaky test" -a "make test_login deterministic" \
  --lane solve --verify "pytest -q tests/test_login.py"
uv run chimera kanban add "Compare REST vs gRPC" --lane crew
uv run chimera kanban board                 # show the columns
uv run chimera kanban run -w ./scratch      # dispatch backlog cards through their lanes
uv run chimera kanban move <id> done        # manual move
uv run chimera kanban learn --min 3 --yes   # recurring tasks (experience) -> cards
```

`run` recorre cada tarjeta backlog → doing → done (éxito) o → review (necesita atención).
`learn` reutiliza el detector de recurrencia del cron-learner para encolar tareas que el agente
repite (deduplicadas contra el tablero) — prográmalo para rellenar el backlog automáticamente.

### `workflow` — bucles diseñados (Loop Engineering)

Diseña un bucle autónomo como YAML en lugar de un prompt improvisado. Cada paso `uses` una
capacidad (`run` / `shell` / `solve` / `crew` / `lifecycle`), puede estar condicionado al paso
anterior (`when: prev_succeeded | prev_failed`), y puede repetirse (`repeat`, `until: success`).

```yaml
# examples/workflow.yaml
name: build-and-report
steps:
  - name: build
    uses: solve
    with: { task: "Create greeting.py with greet(name)", verify: "python -c \"import greeting\"" }
    repeat: 2
    until: success
  - name: report
    uses: run
    when: prev_succeeded
    with: { prompt: "One-line changelog for greet()" }
```

```bash
uv run chimera workflow examples/workflow.yaml --workspace ./scratch
```

### `drift` — barrera de drift spec↔código

Mantiene una especificación y el código alineados. Una spec es un pequeño YAML de requisitos
(`defines` un símbolo / `contains` una regex / `absent` una regex / `command` sale con 0). La
barrera sale con código distinto de cero ante drift, así que también sirve como verificador.

```bash
uv run chimera drift examples/spec.yaml --workspace ./scratch
# as a verifier inside solve:
uv run chimera solve "..." --verify "chimera drift examples/spec.yaml -w ."
```

### `migrate` — importar desde otro agente

Trae **config + skills** desde Hermes u OpenClaw, y con `--apply` también **fusiona la memoria
de largo plazo** (deduplicada, no destructiva). Por defecto es una vista previa en modo
dry-run.

```bash
uv run chimera migrate hermes /path/to/hermes/home          # preview
uv run chimera migrate hermes /path/to/hermes/home --apply  # write + merge memory
uv run chimera migrate openclaw /path/to/openclaw/home --apply
```

La fusión de memoria reporta conteos `{ADD, UPDATE, NOOP}` — los duplicados se convierten en
`NOOP`, así que volver a ejecutarlo es seguro.

### `evolve` — evolución de modelo opcional (avanzado)

`chimera solve --collect` (activado por defecto) registra cada ejecución como una trayectoria.
Los comandos `evolve` convierten eso en datasets listos para entrenamiento y una receta LoRA
ejecutable. **El entrenamiento es externo y opcional** — cambia los pesos del modelo, así que
nunca ocurre automáticamente; Chimera prepara los datos y un script y se detiene.

```bash
chimera evolve status                          # is there enough signal to train?
chimera evolve export --format sft --out d.jsonl --min-steps 5 --diverse   # long-horizon, one example per task
chimera evolve export --format dpo --out d.jsonl   # preference pairs (success vs failure)
chimera evolve recipe --out ./recipe --format dpo  # train.py + README + requirements
chimera evolve tune --rounds 2                  # self-optimize the agent spec (no weights changed)
```

`export` acepta ajustes de receta: `--min-steps N` conserva solo trazas de largo horizonte,
`--diverse` conserva como máximo un ejemplo por tarea (la diversidad de tareas es el cuello de
botella de la curación), y `--min-process P` (SkillCoach) conserva solo trazas cuya puntuación
de *seguimiento de pasos* ≥ P — la fracción de pasos de herramientas que produjeron un
resultado exitoso y visible — así un éxito afortunado que se debatió entre llamadas a
herramientas fallidas no entra al entrenamiento. Los eventos por paso detrás de esa puntuación
se capturan automáticamente en cada ejecución de `solve`; el filtro está desactivado por
defecto (`CHIMERA_SFT_MIN_PROCESS` establece un valor por defecto global). `evolve tune` es
distinto del entrenamiento — ejecuta una **meta-búsqueda** sobre la *especificación* del agente
(modelo, prompt de sistema, presupuesto de pasos, panel, profundidad de memoria), calificando
cada candidato en los escenarios diarios y conservando una edición solo con
**no-regresión**. Llama a modelos pero nunca cambia pesos, así que es seguro ejecutarlo en
cualquier momento.

Luego, para entrenar de verdad, en una GPU (o Colab): `pip install chimera-agent[train]` (o el
`requirements.txt` de la receta) y `python recipe/train.py`. Apunta `CHIMERA_DEFAULT_MODEL` al
modelo base + adapter al servir.

### `pet` — un compañero virtual

Un pequeño compañero persistente cuyas estadísticas van cambiando mientras estás fuera. No
necesita clave.

```bash
chimera pet new --name Chimi      # adopt one
chimera pet status                # check in (fullness / happiness / energy / mood)
chimera pet feed | play | rest    # interact
```

---

## Consejos

- **Herramientas vs. razonamiento.** Los turnos de llamada a herramientas siempre usan un solo
  modelo (la fusión no puede llamar herramientas); la fusión se reserva para razonamiento
  profundo sin herramientas.
- **Inspecciona lo que pasó.** `CHIMERA_LOG_LEVEL=DEBUG` muestra los logs de enrutamiento y
  activación de fusión.
- **Mantén las pruebas honestas.** Un buen comando `--verify` (una suite de pruebas real) hace
  que `solve` sea confiable — es la verdad de referencia ejecutable a la que se atiene el
  agente.
