---
source_sha256: a88090fec9fcabd118b65cf8d40ddecefd47fbb2da49dfa195a66fd57e85c4c1
---

# Recipes

Workflow reali ed eseguibili che fanno qualcosa di utile end-to-end con i tool integrati.
Eseguine una qualsiasi con `chimera workflow <file> -w <workspace>`. Le fonti complete vivono
nella cartella [`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## Completamente locale, senza chiave API (Ollama)

Esegui Chimera contro un modello sulla tua stessa macchina — nessuna chiave, niente esce dalla
scatola. Installa [Ollama](https://ollama.com), scarica un modello, poi punta Chimera verso di
esso:

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera run "Summarise this file in 3 bullets" -w .
```

Tutto qui — niente `OPENROUTER_API_KEY`, niente cloud. Il gate delle credenziali riconosce
`ollama/…` (e `ollama_chat/…`) come runtime locale e lo lascia passare. Se Ollama gira altrove,
imposta `CHIMERA_OLLAMA_BASE_URL=http://host:11434` (default `http://localhost:11434`).

I modelli locali sono più piccoli, quindi questo è l'estremo *debole* della fascia
[goldilocks](../bench/local_lift/RESULTS.md) — adatto a `chimera solve` (il piano +
verifica-o-ripristina aiuta un modello debole) e alla privacy offline, meno a un ragionamento di
frontiera in un colpo solo. Mescola e abbina: un default locale con `CHIMERA_FALLBACK_MODELS`
nel cloud per le chiamate difficili.

## Triage delle email

Legge la tua casella di posta, classifica `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, scrive
un digest di dieci secondi. Solo lettura — nulla viene cancellato, spostato o inviato.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Richiede credenziali IMAP. Configurazione + pianificazione giornaliera:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Brief di ricerca giornaliero

Un argomento in entrata, un brief con fonti in 5 punti + un digest di 3 righe in uscita (arxiv
sempre; ricerca web se è impostata una chiave Tavily).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Watchdog di repository

Esegue la suite di test di un repository e scrive un report di salute nominando ogni test che
fallisce. Solo lettura, eccetto il report.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Leggere documenti (PDF, DOCX, XLSX…)

L'agente legge testo semplice nativamente. Per documenti veri — PDF, Word, PowerPoint, Excel,
HTML, CSV, EPUB — installa l'extra opzionale e guadagna un tool `read_document` che converte
ognuno di essi in Markdown:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Poi punta un task verso un file: *"Riassumi report.pdf in 5 punti."* Senza l'extra,
`read_document` restituisce un suggerimento d'installazione di una riga invece di fallire.

## Navigare sul web (navigare, leggere + agire)

Il tool `browser` è **integrato** — guida un Chromium vero (così vede pagine renderizzate in
JavaScript che il semplice `http_get` non riesce a vedere). Playwright viene incluso con
Chimera; il binario Chromium di ~150MB viene **scaricato automaticamente la prima volta che usi
il browser** (un passo una tantum che pip non può fare per te). Non serve alcun passo
d'installazione:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

Il tool `browser` ha queste azioni:

- **`navigate` / `read`** — apre un URL ed elenca gli elementi *interattivi* della pagina come
  `[ref] role: name` (link, pulsanti, campi), così l'agente clicca/digita per `ref`, non per
  pixel.
- **`read_text`** — il **testo renderizzato completo** della pagina, per leggere/ricercare un
  articolo, documento o risultato. Con l'extra `documents` è **Markdown** pulito (intestazioni,
  link, liste preservati via MarkItDown); senza, è il testo visibile semplice. Passa un `url`
  opzionale per aprire + leggere in un unico passo.
- **`find`** — cerca nel testo renderizzato una query e restituisce le righe corrispondenti.
- **`click` / `type` / `back`** — guida la pagina per `ref`.

`CHIMERA_BROWSER_HEADLESS=false` esegue Chromium con interfaccia visibile per il debug.

Il contenuto della pagina è **non fidato**: ogni risultato è recintato come dato e il tool
contamina l'esecuzione, quindi preferisci `solve --taint --guard` durante la navigazione ed
estrai campi strutturati attraverso il reader in quarantena invece di agire sul testo grezzo
della pagina. Senza l'extra `documents`, `read_text` funziona comunque — solo come testo semplice
invece che Markdown.

## Ricercare un argomento (cercare + leggere)

Combina la ricerca web con il `read_text` del browser per ricercare qualcosa e ottenere un brief
con fonti — `web_search` (richiede `CHIMERA_TAVILY_API_KEY`) trova le pagine, `browser read_text`
legge ognuna (inclusi siti pesanti in JS), e `deliver` scrive il brief:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Per una versione già pronta con un controllo eseguibile per passo, vedi
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief) — usa `arxiv_search` +
`web_search` nativamente, e con l'extra `browser` installato l'agente può anche fare
`read_text` di pagine complete invece di fermarsi agli snippet di ricerca.

## Scraping & estrazione strutturata sicura

Due tool integrati trasformano qualsiasi pagina in dati puliti, pronti per l'LLM — nessun extra
da installare:

- **`scrape`** — recupera un URL e restituisce **Markdown + metadati** puliti. Percorre una
  cascata consapevole dei costi: prima un semplice GET HTTP, con escalation al **browser**
  integrato (rendering JS) se la pagina torna vuota, e — solo se imposti `FIRECRAWL_API_KEY` —
  ripiegando su **Firecrawl** per pagine pesanti con anti-bot. `render=http|browser|firecrawl`
  forza un backend specifico; `include_links` restituisce anche i link della pagina.
- **`extract`** — estrae campi specifici come **JSON validato**, in sicurezza. Dagli un `url`
  (o `content`) e una lista di `fields` (es. `["title", "price", "author"]`) e restituisce
  *solo* quei campi. Fondamentalmente, legge la pagina attraverso il **reader in quarantena** di
  Chimera — un modello senza tool il cui output è validato da schema — così **le istruzioni
  nascoste nella pagina non possono dirottare l'agente**. Questa è la garanzia di sicurezza che
  Firecrawl/ScrapeGraphAI non ti danno: una pagina ostile può al massimo restituire un valore
  sbagliato, mai una nuova istruzione. Le pagine grandi sono suddivise in blocchi e unite,
  fermandosi presto una volta che ogni campo è riempito per limitare il costo. Per un
  **template di pagina noto**, passa `selectors` (campo → CSS, es.
  `{"price": ".price", "link": "a.more::attr(href)"}`) e quei campi vengono estratti **in modo
  deterministico — gratis, senza LLM** — con l'LLM sicuro usato solo per i campi che un
  selettore non ha riempito.

```bash
uv run chimera run "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera run "extract the fields title, price, availability from https://example.com/product --taint"
```

Per interi siti ci sono altri due verbi:

- **`map`** — elenca gli URL di un sito a basso costo (legge la sitemap quando esiste, altrimenti
  scansiona i link della pagina). Filtro opzionale per parola chiave `search`. Eseguilo per
  delimitare un sito prima di farne il crawl.
- **`crawl`** — segue i link a partire da un URL seme e restituisce il Markdown pulito di ogni
  pagina. Limitato da `limit` e `max_depth`, ristretto allo stesso dominio per default, e
  **consapevole di robots.txt** (rispetta `Disallow` e `Crawl-delay`). `include`/`exclude` sono
  pattern glob di URL. I crawl lunghi sono **ripristinabili**: la frontiera viene salvata su
  disco dopo ogni pagina, così un crawl interrotto alla pagina N continua dalla N+1 alla
  prossima esecuzione (`resume=true` per default).

```bash
uv run chimera run "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Tutto è recintato come dato e contamina l'esecuzione (è contenuto web non fidato), quindi
`solve --taint --guard` è il modo sicuro per agire su di esso. Il fallback opzionale di
Firecrawl viene usato *solo* quando il motore integrato non riesce a recuperare una pagina e la
chiave è impostata — Chimera raschia da solo la grande maggioranza del web, senza servizi
esterni.

## Audio: da voce a testo (trascrizione)

Chimera può trasformare la voce in testo — il partner simmetrico dei suoi tool di generazione
immagini e text-to-speech. **Orchestra un modello Whisper** (non lo addestra): il tool
`transcribe_audio` usa il **faster-whisper** locale se installi l'extra `stt`
(offline/privato), altrimenti l'API Whisper ospitata di OpenAI (richiede una chiave OpenAI):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera run "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Una nota sull'ambito, nello spirito onesto di questo progetto: Chimera è un **agente**, non un
> modello. Può *usare* speech-to-text, generazione immagini, visione artificiale, o ML classico
> — chiamando un'API o eseguendo una libreria nella sua sandbox di codice — ma non
> *reimplementa* (e non avrebbe senso farlo) Whisper, Stable Diffusion, PyTorch, o OpenCV. Per la
> data science / ML, la sandbox `execute_code` permette già all'agente di scrivere ed eseguire
> Python contro scikit-learn, pandas, OpenCV, ecc. L'orchestrazione moltiplica l'agente; la
> reimplementazione produrrebbe solo una copia più lenta.

## Scaricare un video o il suo audio

Il tool `download_media` estrae un video (o solo il suo audio) da YouTube e oltre 1000 altri
siti nel workspace. Avvolge **yt-dlp** (mantenuto attivamente, gestisce il continuo cambiamento
di cifratura/formato/age-gate che affossa gli scraper a sito singolo come pytube). Opt-in;
l'estrazione audio richiede anche `ffmpeg` nel PATH:

```bash
uv sync --extra media-dl
uv run chimera run "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Si abbina naturalmente a `transcribe_audio` sopra: scaricare → trascrivere → riassumere, tutto
in un'unica esecuzione.

## Analisi dati / ML (la skill `data_analysis`)

Chimera non reimplementa scikit-learn — **scrive codice pandas/sklearn corretto e lo esegue**
nella sandbox `execute_code`. La skill `data_analysis` nomina questa capacità: dalle un task e
un dataset ed emette uno script autonomo (carica → esplora → modella → valuta) che l'agente poi
esegue.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera run "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Generazione immagini (ospitata o completamente locale)

`generate_image` usa l'API immagini di OpenAI per default. Per una configurazione
**offline/privata**, imposta `CHIMERA_IMAGE_BACKEND=local` e installa l'extra (pesante,
dipendente da GPU) `imagegen-local` — Chimera allora esegue **FLUX.1-schnell** (Apache-2.0)
tramite `diffusers` in locale. `auto` (il default) usa il locale solo quando non è presente
alcuna chiave OpenAI.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera run "generate an image of a fox in a snowy forest"
```

> Stesso ambito onesto di prima: Chimera qui *esegue* un modello di diffusione; non ne addestra
> uno. La generazione video (es. CogVideo) deliberatamente **non** è integrata — è un modello
> addestrato pesante, non qualcosa che un agente dovrebbe portare nella sua base; ricorri a
> un'API ospitata se mai ne avrai bisogno. La visione artificiale (OpenCV) non richiede un tool
> dedicato — l'agente fa già `import cv2` nella sandbox di codice.

## Grafici & visualizzazione dati

Due modi complementari per fare un grafico — entrambi onesti sull'ambito (Chimera *usa*
librerie di plotting; non reimplementa matplotlib/plotly/bokeh):

**1. La skill `data_visualization` — scrive codice per il grafico, lo esegue nella sandbox.**
Copre *tutto* (figure personalizzate/da pubblicazione, 3D, qualsiasi cosa): la skill emette uno
script autonomo usando matplotlib/seaborn (PNG/SVG statico) o plotly (HTML interattivo), con il
backend headless (`matplotlib.use("Agg")`) e la disciplina del salvataggio nel workspace già
integrati.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera run "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. Il tool `render_chart` — una specifica Vega-Lite sicura e dichiarativa.** Una spec
Vega-Lite è **JSON inerte, non codice**: ispezionabile, dalla forma di schema, e ri-renderizzabile
— una storia di governance più forte rispetto all'eseguire codice generato, per i grafici
standard che Vega-Lite copre (barre/linea/dispersione/istogramma/mappa di calore/sfaccettato…).
**L'output HTML non richiede alcun extra** (incorpora la spec + il CDN di Vega); PNG/SVG usano
l'extra opzionale `viz-vega` (`vl-convert-python`).

```bash
uv run chimera run "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Ambito onesto: plotly avvolge plotly.js, bokeh è per metà TypeScript, il renderer di
> matplotlib è C++, e seaborn è uno strato sottile sopra matplotlib — tutti framework che un
> agente dovrebbe *chiamare*, non riscrivere. La sandbox di codice li importa già; la skill si
> limita a nominare la capacità e a gestire le insidie della modalità headless. Vega-Lite è
> l'eccezione che merita un tool dedicato perché il suo artefatto è dato dichiarativo sicuro.

## Pianificare una qualsiasi di esse

Ogni recipe può girare su un cron e consegnare in chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

Vedi [Deploy](deploy.md) per il gateway di messaggistica e la configurazione 24/7.
