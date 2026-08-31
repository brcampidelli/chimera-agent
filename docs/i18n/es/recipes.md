---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# Recipes

Flujos de trabajo reales y ejecutables que hacen algo útil de principio a fin con las
herramientas incorporadas. Ejecuta cualquiera con `chimera workflow <file> -w <workspace>`. Las
fuentes completas viven en la carpeta
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## Totalmente local, sin clave de API (Ollama)

Ejecuta Chimera contra un modelo en tu propia máquina — sin clave, nada sale de la caja.
Instala [Ollama](https://ollama.com), descarga un modelo, y luego apunta Chimera hacia él:

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_DEFAULT_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera agent "Summarise this file in 3 bullets" -w .
```

Eso es todo — sin `OPENROUTER_API_KEY`, sin nube. La barrera de credenciales reconoce
`ollama/…` (y `ollama_chat/…`) como un runtime local y lo deja pasar. Si Ollama corre en otro
lugar, configura `CHIMERA_OLLAMA_BASE_URL=http://host:11434` (por defecto
`http://127.0.0.1:11434`).

Los modelos locales son más pequeños, así que este es el extremo *débil* del rango
[goldilocks](../bench/local_lift/RESULTS.md) — un buen ajuste para `chimera solve` (el plan +
verify-or-revert ayuda a un modelo débil) y para privacidad offline, menos para razonamiento de
frontera en un solo intento. Combínalos: un modelo local por defecto con
`CHIMERA_FALLBACK_MODELS` en la nube para las llamadas difíciles.

## Triaje de correo

Lee tu bandeja de entrada, clasifica `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, escribe un
resumen de diez segundos. Solo lectura — nada se elimina, mueve o envía.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Necesita credenciales IMAP. Configuración + programación diaria:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Resumen diario de investigación

Un tema entra, sale un informe de 5 puntos con fuentes + un resumen de 3 líneas (arxiv siempre;
búsqueda web si hay una clave de Tavily configurada).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Vigilante de repositorio

Ejecuta la suite de pruebas de un repo y escribe un informe de salud nombrando las pruebas que
fallan. Solo lectura, excepto el informe.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Lectura de documentos (PDF, DOCX, XLSX…)

El agente lee texto plano de fábrica. Para documentos reales — PDF, Word, PowerPoint, Excel,
HTML, CSV, EPUB — instala el extra opcional y gana una herramienta `read_document` que convierte
cualquiera de ellos a Markdown:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Luego apunta una tarea a un archivo: *"Summarize report.pdf into 5 bullets."* Sin el extra,
`read_document` devuelve una sugerencia de instalación de una línea en lugar de fallar.

## Navegar la web (navegar, leer + actuar)

La herramienta `browser` está **incorporada** — maneja un Chromium real (así que ve páginas
renderizadas con JavaScript que el `http_get` plano no puede). Playwright viene con Chimera; el
binario de Chromium de ~150MB se **descarga automáticamente la primera vez que usas el
navegador** (un paso único que pip no puede hacer por ti). No se necesita ningún paso de
instalación:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

La herramienta `browser` tiene estas acciones:

- **`navigate` / `read`** — abre una URL y lista los elementos *interactivos* de la página como
  `[ref] role: name` (enlaces, botones, campos), así el agente hace clic/escribe por `ref`, no
  por píxel.
- **`read_text`** — el **texto completo renderizado** de la página, para leer/investigar un
  artículo, documento o resultado. Con el extra `documents` es **Markdown** limpio (encabezados,
  enlaces, listas preservados vía MarkItDown); sin él, el texto visible plano. Pasa una `url`
  opcional para abrir + leer en un solo paso.
- **`find`** — busca en el texto renderizado una consulta y devuelve las líneas coincidentes.
- **`click` / `type` / `back`** — maneja la página por `ref`.

`CHIMERA_BROWSER_HEADLESS=false` ejecuta Chromium con interfaz visible para depuración.

El contenido de la página es **no confiable**: cada resultado queda cercado como datos y la
herramienta contamina (taint) la ejecución, así que prefiere `solve --taint --guard` al navegar
y extrae campos estructurados a través del lector en cuarentena en lugar de actuar sobre el
texto crudo de la página. Sin el extra `documents`, `read_text` sigue funcionando — solo que como
texto plano en lugar de Markdown.

## Investigar un tema (buscar + leer)

Combina la búsqueda web con el `read_text` del navegador para investigar algo y obtener un
informe con fuentes — `web_search` (necesita `TAVILY_API_KEY`) encuentra las páginas,
`browser read_text` lee cada una (incluyendo sitios pesados en JS), y `deliver` escribe el
informe:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Para una versión lista para usar con una comprobación ejecutable por paso, consulta
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief) — usa `arxiv_search` +
`web_search` de fábrica, y con el extra `browser` instalado el agente también puede hacer
`read_text` de páginas completas en lugar de detenerse en los fragmentos de búsqueda.

## Scraping y extracción estructurada segura

Dos herramientas incorporadas convierten cualquier página en datos limpios y listos para LLM —
sin ningún extra que instalar:

- **`scrape`** — obtiene una URL y devuelve **Markdown + metadatos** limpios. Recorre una
  cascada consciente del costo: primero un GET HTTP plano, escalando al **navegador**
  incorporado (renderizado JS) si la página vuelve vacía, y — solo si configuras
  `FIRECRAWL_API_KEY` — recurriendo a **Firecrawl** para páginas pesadas anti-bot.
  `render=http|browser|firecrawl` fuerza un backend específico; `include_links` también
  devuelve los enlaces de la página.
- **`extract`** — extrae campos específicos como **JSON validado**, de forma segura. Dale una
  `url` (o `content`) y una lista de `fields` (p. ej. `["title", "price", "author"]`) y devuelve
  *solo* esos campos. Crucialmente, lee la página a través del **lector en cuarentena** de
  Chimera — un modelo sin herramientas cuya salida está validada por esquema — así que **las
  instrucciones escondidas en la página no pueden secuestrar al agente**. Esa es la garantía de
  seguridad que Firecrawl/ScrapeGraphAI no te dan: una página hostil puede, en el peor caso,
  devolver un valor incorrecto, nunca una instrucción nueva. Las páginas grandes se dividen en
  fragmentos y se combinan, deteniéndose temprano en cuanto cada campo se llena para acotar el
  costo. Para una **plantilla de página conocida**, pasa `selectors` (campo → CSS, p. ej.
  `{"price": ".price", "link": "a.more::attr(href)"}`) y esos campos se extraen
  **deterministamente — gratis, sin LLM** — usando el LLM seguro solo para los campos que un
  selector no llenó.

```bash
uv run chimera agent "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera agent "extract the fields title, price, availability from https://example.com/product --taint"
```

Para sitios completos hay dos verbos más:

- **`map`** — lista las URLs de un sitio de forma económica (lee el sitemap cuando existe, si
  no, escanea los enlaces de la página). Filtro opcional por palabra clave `search`. Ejecuta
  esto para delimitar un sitio antes de rastrearlo (crawl).
- **`crawl`** — sigue enlaces desde una URL semilla y devuelve el Markdown limpio de cada
  página. Acotado por `limit` y `max_depth`, del mismo dominio por defecto, y **respetuoso de
  robots.txt** (obedece `Disallow` y `Crawl-delay`). `include`/`exclude` son patrones glob de
  URL. Los rastreos largos son **reanudables**: la frontera se guarda en disco después de cada
  página, así que un rastreo interrumpido en la página N continúa desde N+1 en la siguiente
  ejecución (`resume=true` por defecto).

```bash
uv run chimera agent "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Todo queda cercado como datos y contamina (taint) la ejecución (es contenido web no confiable),
así que `solve --taint --guard` es la forma segura de actuar sobre él. El respaldo opcional de
Firecrawl se usa *solo* cuando el motor incorporado no puede obtener una página y la clave está
configurada — Chimera hace scraping de la gran mayoría de la web por sí mismo, sin servicio
externo.

## Audio: voz a texto (transcripción)

Chimera puede convertir voz en texto — el compañero simétrico de sus herramientas de generación
de imágenes y texto a voz. **Orquesta un modelo Whisper** (no entrena uno): la herramienta
`transcribe_audio` usa **faster-whisper** local si instalas el extra `stt` (offline/privado), o
si no, la API alojada de OpenAI Whisper (necesita una clave de OpenAI):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera agent "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Una nota sobre el alcance, en el espíritu honesto de este proyecto: Chimera es un **agente**,
> no un modelo. Puede *usar* voz a texto, generación de imágenes, visión por computadora, o ML
> clásico — llamando a una API o ejecutando una librería en su sandbox de código — pero no
> *reimplementa* (ni tendría sentido que lo hiciera) Whisper, Stable Diffusion, PyTorch, u
> OpenCV. Para ciencia de datos / ML, el sandbox `execute_code` ya deja al agente escribir y
> ejecutar Python contra scikit-learn, pandas, OpenCV, etc. La orquestación multiplica al
> agente; la reimplementación solo produciría una copia más lenta.

## Descargar un video o su audio

La herramienta `download_media` extrae un video (o solo su audio) de YouTube y de más de 1000
sitios hacia el workspace. Envuelve **yt-dlp** (mantenido activamente, maneja los cambios de
cifrado/formato/verificación de edad que hunden a los scrapers de un solo sitio como pytube).
Opcional; la extracción de audio también necesita `ffmpeg` en el PATH:

```bash
uv sync --extra media-dl
uv run chimera agent "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Combina naturalmente con `transcribe_audio` de arriba: descargar → transcribir → resumir, todo
en una sola ejecución.

## Análisis de datos / ML (la skill `data_analysis`)

Chimera no reimplementa scikit-learn — **escribe código pandas/sklearn correcto y lo ejecuta**
en el sandbox `execute_code`. La skill `data_analysis` nombra esa capacidad: dale una tarea y un
dataset y emite un script autocontenido (cargar → explorar → modelar → evaluar) que el agente
luego ejecuta.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera agent "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Generación de imágenes (alojada o totalmente local)

`generate_image` usa la API de imágenes de OpenAI por defecto. Para una configuración
**offline / privada**, configura `CHIMERA_IMAGE_BACKEND=local` e instala el extra
`imagegen-local` (pesado, ligado a GPU) — Chimera entonces ejecuta **FLUX.1-schnell**
(Apache-2.0) vía `diffusers` localmente. `auto` (el valor por defecto) usa local solo cuando no
hay una clave de OpenAI presente.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera agent "generate an image of a fox in a snowy forest"
```

> Mismo alcance honesto que arriba: Chimera *ejecuta* un modelo de difusión aquí; no entrena
> uno. La generación de video (p. ej. CogVideo) deliberadamente **no** viene incorporada — es un
> modelo entrenado pesado, no algo que un agente deba llevar en su base; recurre a una API
> alojada si alguna vez la necesitas. La visión por computadora (OpenCV) no necesita ninguna
> herramienta dedicada — el agente ya hace `import cv2` en el sandbox de código.

## Gráficos y visualización de datos

Dos formas complementarias de hacer un gráfico — ambas honestas sobre su alcance (Chimera *usa*
librerías de graficación; no reimplementa matplotlib/plotly/bokeh):

**1. La skill `data_visualization` — escribe código de gráfico, lo ejecuta en el sandbox.**
Cubre *todo* (figuras personalizadas/de publicación, 3D, lo que sea): la skill emite un script
autocontenido usando matplotlib/seaborn (PNG/SVG estático) o plotly (HTML interactivo), con el
backend headless (`matplotlib.use("Agg")`) y la disciplina de guardar en el workspace ya
incorporadas.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera agent "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. La herramienta `render_chart` — una especificación Vega-Lite segura y declarativa.** Una
especificación Vega-Lite es **JSON inerte, no código**: inspeccionable, con forma de esquema, y
re-renderizable — una historia de gobernanza más sólida que ejecutar código generado, para los
gráficos estándar que cubre Vega-Lite (barras/líneas/dispersión/histograma/mapa de
calor/facetado…). **La salida HTML no necesita ningún extra** (incrusta la especificación + el
CDN de Vega); PNG/SVG usan el extra opcional `viz-vega` (`vl-convert-python`).

```bash
uv run chimera agent "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Alcance honesto: plotly envuelve plotly.js, bokeh es más o menos mitad TypeScript, el
> renderizador de matplotlib es C++, y seaborn es una capa delgada sobre matplotlib — todos
> frameworks que un agente debe *llamar*, no reescribir. El sandbox de código ya los importa; la
> skill solo nombra la capacidad y maneja las trampas del modo headless. Vega-Lite es la
> excepción que merece una herramienta dedicada porque su artefacto son datos declarativos
> seguros.

## Programa cualquiera de ellas

Cada recipe puede correr en un cron y entregar a chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

Consulta [Despliegue](deploy.md) para el gateway de mensajería y la configuración 24/7.
