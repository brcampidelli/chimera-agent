---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# Recipes

Echte, lauffähige Workflows, die mit den eingebauten Tools von Anfang bis Ende etwas Nützliches
erledigen. Jeden mit `chimera workflow <file> -w <workspace>` ausführen. Vollständige Quellen
liegen im Ordner
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## Vollständig lokal, kein API-Key (Ollama)

Chimera gegen ein Modell auf der eigenen Maschine ausführen — kein Key, nichts verlässt die
Box. [Ollama](https://ollama.com) installieren, ein Modell ziehen, dann Chimera darauf
verweisen:

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_DEFAULT_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera agent "Summarise this file in 3 bullets" -w .
```

Das war's — kein `OPENROUTER_API_KEY`, keine Cloud. Das Credential-Gate erkennt `ollama/…` (und
`ollama_chat/…`) als lokale Laufzeit und lässt es durch. Läuft Ollama woanders,
`CHIMERA_OLLAMA_BASE_URL=http://host:11434` setzen (Standard `http://127.0.0.1:11434`).

Lokale Modelle sind kleiner, das hier ist also das *schwache* Ende der
[Goldlöckchen](../bench/local_lift/RESULTS.md)-Spanne — gut geeignet für `chimera solve`
(Plan + verify-or-revert hilft einem schwachen Modell) und für Offline-Privatsphäre, weniger für
einmaliges Frontier-Reasoning. Mischen und kombinieren: ein lokaler Default mit einem Cloud-
`CHIMERA_FALLBACK_MODELS` für die schwierigen Fälle.

## E-Mail-Triage

Postfach lesen, `URGENT / PERSONAL / NEWSLETTER / COLD-SALES` klassifizieren, einen
Zehn-Sekunden-Digest schreiben. Nur lesend — nichts wird gelöscht, verschoben oder gesendet.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Braucht IMAP-Zugangsdaten. Einrichtung + tägliche Planung:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Täglicher Research-Brief

Ein Thema rein, ein 5-Punkte-Brief mit Quellen + ein 3-Zeilen-Digest raus (Arxiv immer; Web-
Suche, wenn ein Tavily-Key gesetzt ist).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Repo-Watchdog

Die Testsuite eines Repos ausführen und einen Health-Report schreiben, der fehlschlagende Tests
benennt. Nur lesend, außer dem Report.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Dokumente lesen (PDF, DOCX, XLSX…)

Der Agent liest Klartext von Haus aus. Für echte Dokumente — PDF, Word, PowerPoint, Excel,
HTML, CSV, EPUB — das optionale Extra installieren, und er erhält ein `read_document`-Tool, das
jedes davon in Markdown umwandelt:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Dann eine Aufgabe auf eine Datei richten: *"Summarize report.pdf into 5 bullets."* Ohne das
Extra gibt `read_document` statt eines Fehlschlags einen einzeiligen Installationshinweis
zurück.

## Im Web browsen (navigieren, lesen + handeln)

Das `browser`-Tool ist **eingebaut** — es steuert ein echtes Chromium (sieht also
JavaScript-gerenderte Seiten, die das einfache `http_get` nicht sieht). Playwright wird mit
Chimera ausgeliefert; die ~150-MB-Chromium-Binärdatei wird **beim ersten Gebrauch des Browsers
automatisch heruntergeladen** (ein einmaliger Schritt, den pip nicht für dich erledigen kann).
Kein Installationsschritt nötig:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

Das `browser`-Tool hat folgende Aktionen:

- **`navigate` / `read`** — eine URL öffnen und die *interaktiven* Elemente der Seite als
  `[ref] role: name` auflisten (Links, Buttons, Felder), sodass der Agent per `ref` klickt/
  tippt, nicht per Pixel.
- **`read_text`** — der **vollständige gerenderte Text** der Seite, zum Lesen/Recherchieren
  eines Artikels, Dokuments oder Ergebnisses. Mit dem `documents`-Extra sauberes **Markdown**
  (Überschriften, Links, Listen bleiben via MarkItDown erhalten); ohne, der reine sichtbare
  Text. Ein optionales `url` übergeben, um in einem Schritt zu öffnen + zu lesen.
- **`find`** — den gerenderten Text nach einer Anfrage durchsuchen und die passenden Zeilen
  zurückbekommen.
- **`click` / `type` / `back`** — die Seite per `ref` steuern.

`CHIMERA_BROWSER_HEADLESS=false` lässt Chromium zum Debuggen mit sichtbarem Fenster laufen.

Seiteninhalt ist **nicht vertrauenswürdig**: jedes Ergebnis ist data-fenced und das Tool
kontaminiert den Lauf, daher beim Browsen `solve --taint --guard` bevorzugen und strukturierte
Felder über den unter Quarantäne stehenden Reader ziehen, statt auf rohem Seitentext zu handeln.
Ohne das `documents`-Extra funktioniert `read_text` weiterhin — nur als reiner Text statt
Markdown.

## Ein Thema recherchieren (suchen + lesen)

Web-Suche mit dem `read_text` des Browsers kombinieren, um etwas zu recherchieren und einen
Brief mit Quellen zu bekommen — `web_search` (braucht `TAVILY_API_KEY`) findet die
Seiten, `browser read_text` liest jede davon (auch JS-lastige Seiten), und `deliver` schreibt
den Brief:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Für eine fertige Version mit einer ausführbaren Prüfung pro Schritt siehe
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief) — sie nutzt von Haus aus `arxiv_search` +
`web_search`, und mit installiertem `browser`-Extra kann der Agent auch ganze Seiten per
`read_text` lesen, statt bei Suchausschnitten stehenzubleiben.

## Scraping & sichere strukturierte Extraktion

Zwei eingebaute Tools verwandeln jede Seite in saubere, LLM-taugliche Daten — kein Extra
nötig:

- **`scrape`** — eine URL abrufen und sauberes **Markdown + Metadaten** zurückgeben. Es
  durchläuft eine kostenbewusste Kaskade: zuerst ein einfacher HTTP-GET, eskaliert zum
  eingebauten **Browser** (JS-Rendering), falls die Seite leer zurückkommt, und — nur wenn
  `FIRECRAWL_API_KEY` gesetzt ist — fällt zurück auf **Firecrawl** für schwer zu knackende
  Anti-Bot-Seiten. `render=http|browser|firecrawl` erzwingt ein bestimmtes Backend;
  `include_links` gibt zusätzlich die Links der Seite zurück.
- **`extract`** — bestimmte Felder sicher als **validiertes JSON** herausziehen. Eine `url`
  (oder `content`) und eine Liste von `fields` übergeben (z. B.
  `["title", "price", "author"]`), und es werden *nur* diese Felder zurückgegeben.
  Entscheidend: Es liest die Seite über Chimeras **unter Quarantäne stehenden Reader** — ein
  werkzeugloses Modell, dessen Ausgabe schema-validiert ist —, sodass **in der Seite versteckte
  Anweisungen den Agenten nicht kapern können**. Das ist die Sicherheitsgarantie, die
  Firecrawl/ScrapeGraphAI nicht bieten: eine feindliche Seite kann schlimmstenfalls einen
  falschen Wert liefern, nie eine neue Anweisung. Große Seiten werden in Chunks verarbeitet und
  zusammengeführt, wobei früh gestoppt wird, sobald jedes Feld gefüllt ist, um die Kosten zu
  deckeln. Für eine **bekannte Seitenvorlage** `selectors` übergeben (Feld → CSS, z. B.
  `{"price": ".price", "link": "a.more::attr(href)"}`), und diese Felder werden
  **deterministisch extrahiert — kostenlos, kein LLM** — wobei das sichere LLM nur für Felder
  genutzt wird, die ein Selektor nicht gefüllt hat.

```bash
uv run chimera agent "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera agent "extract the fields title, price, availability from https://example.com/product --taint"
```

Für ganze Websites gibt es zwei weitere Verben:

- **`map`** — die URLs einer Site günstig auflisten (liest die Sitemap, wenn vorhanden, sonst
  scannt es die Links der Seite). Optionaler `search`-Schlüsselwortfilter. Damit eine Site
  abstecken, bevor sie gecrawlt wird.
- **`crawl`** — Links von einer Start-URL aus verfolgen und das saubere Markdown jeder Seite
  zurückgeben. Begrenzt durch `limit` und `max_depth`, standardmäßig auf derselben Domain, und
  **robots.txt-bewusst** (befolgt `Disallow` und `Crawl-delay`). `include`/`exclude` sind
  URL-Glob-Muster. Lange Crawls sind **fortsetzbar**: die Warteschlange wird nach jeder Seite
  auf die Festplatte geschrieben, sodass ein bei Seite N unterbrochener Crawl beim nächsten Lauf
  ab N+1 weitermacht (`resume=true` standardmäßig).

```bash
uv run chimera agent "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Alles ist data-fenced und kontaminiert den Lauf (es ist nicht vertrauenswürdiger Webinhalt),
daher ist `solve --taint --guard` der sichere Weg, darauf zu handeln. Der optionale
Firecrawl-Fallback wird *nur* genutzt, wenn die eingebaute Engine eine Seite nicht abrufen kann
und der Key gesetzt ist — Chimera scrapt den Großteil des Webs selbst, ohne externen Dienst.

## Audio: Sprache-zu-Text (Transkription)

Chimera kann Sprache in Text verwandeln — das symmetrische Gegenstück zu seinen
Bildgenerierungs- und Text-zu-Sprache-Tools. Es **orchestriert ein Whisper-Modell** (trainiert
keins): Das `transcribe_audio`-Tool nutzt lokal **faster-whisper**, wenn das `stt`-Extra
installiert ist (offline/privat), sonst die gehostete OpenAI-Whisper-API (braucht einen
OpenAI-Key):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera agent "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Eine Anmerkung zum Umfang, im ehrlichen Geist dieses Projekts: Chimera ist ein **Agent**, kein
> Modell. Es kann Sprache-zu-Text, Bildgenerierung, Computer Vision oder klassisches ML
> *nutzen* — durch den Aufruf einer API oder das Ausführen einer Bibliothek in seiner
> Code-Sandbox —, aber es *reimplementiert* Whisper, Stable Diffusion, PyTorch oder OpenCV
> nicht (und kann das auch sinnvollerweise nicht). Für Data Science / ML erlaubt die
> `execute_code`-Sandbox dem Agenten bereits, Python gegen scikit-learn, pandas, OpenCV usw. zu
> schreiben und auszuführen. Orchestrierung vervielfacht den Agenten; Reimplementierung würde
> nur eine langsamere Kopie erzeugen.

## Ein Video oder dessen Audio herunterladen

Das `download_media`-Tool zieht ein Video (oder nur dessen Audio) von YouTube und über 1000
weiteren Seiten in den Workspace. Es umhüllt **yt-dlp** (aktiv gepflegt, kommt mit dem
ständigen Wandel von Cipher/Format/Age-Gate klar, der Single-Site-Scraper wie pytube versenkt).
Opt-in; Audio-Extraktion braucht außerdem `ffmpeg` im PATH:

```bash
uv sync --extra media-dl
uv run chimera agent "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Passt natürlich zu `transcribe_audio` von oben: herunterladen → transkribieren →
zusammenfassen, alles in einem Lauf.

## Datenanalyse / ML (der `data_analysis`-Skill)

Chimera reimplementiert scikit-learn nicht — es **schreibt korrekten pandas-/sklearn-Code und
führt ihn aus** in der `execute_code`-Sandbox. Der `data_analysis`-Skill benennt diese
Fähigkeit: ihm eine Aufgabe und einen Datensatz geben, und er erzeugt ein in sich
abgeschlossenes Skript (laden → explorieren → modellieren → auswerten), das der Agent dann
ausführt.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera agent "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Bildgenerierung (gehostet oder vollständig lokal)

`generate_image` nutzt standardmäßig die OpenAI-Bild-API. Für ein **Offline-/privates**
Setup `CHIMERA_IMAGE_BACKEND=local` setzen und das (schwere, GPU-gebundene)
`imagegen-local`-Extra installieren — Chimera führt dann lokal **FLUX.1-schnell**
(Apache-2.0) via `diffusers` aus. `auto` (der Standard) nutzt lokal nur, wenn kein
OpenAI-Key vorhanden ist.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera agent "generate an image of a fox in a snowy forest"
```

> Derselbe ehrliche Umfang wie oben: Chimera *führt* hier ein Diffusionsmodell *aus*; es
> trainiert keins. Videogenerierung (z. B. CogVideo) ist absichtlich **nicht** eingebaut — es
> ist ein schwergewichtiges trainiertes Modell, nichts, was ein Agent in seiner Basis tragen
> sollte; bei Bedarf zu einer gehosteten API greifen. Computer Vision (OpenCV) braucht kein
> dediziertes Tool — der Agent macht in der Code-Sandbox bereits `import cv2`.

## Diagramme & Datenvisualisierung

Zwei sich ergänzende Wege, ein Diagramm zu erstellen — beide ehrlich über ihren Umfang
(Chimera *nutzt* Plotting-Bibliotheken; es reimplementiert matplotlib/plotly/bokeh nicht):

**1. Der `data_visualization`-Skill — Diagramm-Code schreiben, in der Sandbox ausführen.**
Deckt *alles* ab (individuelle/publikationsreife Abbildungen, 3D, alles): Der Skill erzeugt ein
in sich abgeschlossenes Skript mit matplotlib/seaborn (statisches PNG/SVG) oder plotly
(interaktives HTML), mit dem Headless-Backend (`matplotlib.use("Agg")`) und der
Speichern-im-Workspace-Disziplin eingebacken.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera agent "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. Das `render_chart`-Tool — eine sichere, deklarative Vega-Lite-Spec.** Eine Vega-Lite-Spec
ist **inerte JSON-Daten, kein Code**: inspizierbar, schemaförmig und erneut renderbar — eine
stärkere Governance-Geschichte als das Ausführen generierten Codes, für die Standarddiagramme,
die Vega-Lite abdeckt (Balken/Linie/Streu/Histogramm/Heatmap/facettiert…). **HTML-Ausgabe
braucht kein Extra** (bettet die Spec + das Vega-CDN ein); PNG/SVG nutzen das optionale
`viz-vega`-Extra (`vl-convert-python`).

```bash
uv run chimera agent "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Ehrlicher Umfang: plotly umhüllt plotly.js, bokeh ist zur Hälfte TypeScript, der Renderer von
> matplotlib ist C++, und seaborn ist eine dünne Schicht über matplotlib — allesamt Frameworks,
> die ein Agent *aufrufen*, nicht neu schreiben sollte. Die Code-Sandbox importiert sie
> bereits; der Skill benennt nur die Fähigkeit und kümmert sich um die Headless-Fallstricke.
> Vega-Lite ist die Ausnahme, die ein dediziertes Tool wert ist, weil sein Artefakt sichere
> deklarative Daten sind.

## Eines davon planen

Jedes Recipe kann per Cron laufen und an Chat ausliefern:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

Siehe [Deploy](deploy.md) für das Messaging-Gateway und das 24/7-Setup.
