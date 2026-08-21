---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# Przepisy

Prawdziwe, uruchamialne workflowy, które robią coś użytecznego od początku do końca za pomocą
wbudowanych narzędzi. Uruchom dowolny przez `chimera workflow <file> -w <workspace>`. Pełne
źródła żyją w folderze
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## W pełni lokalnie, bez klucza API (Ollama)

Uruchom Chimerę na modelu na własnej maszynie — bez klucza, nic nie opuszcza urządzenia.
Zainstaluj [Ollama](https://ollama.com), pobierz model, potem skieruj na niego Chimerę:

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera agent "Summarise this file in 3 bullets" -w .
```

I to wszystko — bez `OPENROUTER_API_KEY`, bez chmury. Brama poświadczeń rozpoznaje `ollama/…`
(oraz `ollama_chat/…`) jako lokalny runtime i przepuszcza go. Jeśli Ollama działa gdzie indziej,
ustaw `CHIMERA_OLLAMA_BASE_URL=http://host:11434` (domyślnie `http://localhost:11434`).

Modele lokalne są mniejsze, więc jest to *słabszy* koniec zakresu
[goldilocks](../bench/local_lift/RESULTS.md) — dobrze pasuje do `chimera solve` (plan +
verify-or-revert pomaga słabemu modelowi) i do prywatności offline, gorzej do jednorazowego
rozumowania na poziomie frontier. Miksuj: lokalny domyślny model z chmurowymi
`CHIMERA_FALLBACK_MODELS` do trudnych wywołań.

## Triage e-maili

Czyta twoją skrzynkę, klasyfikuje `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, pisze
dziesięciosekundowy digest. Tylko do odczytu — nic nie jest usuwane, przenoszone ani wysyłane.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Wymaga poświadczeń IMAP. Konfiguracja + codzienne planowanie:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Codzienny brief badawczy

Temat na wejściu, 5-punktowy brief ze źródłami + 3-liniowy digest na wyjściu (arxiv zawsze;
wyszukiwanie webowe, jeśli ustawiony jest klucz Tavily).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Watchdog repozytorium

Uruchamia zestaw testów repozytorium i pisze raport zdrowia wymieniający wszystkie zawodzące
testy. Tylko do odczytu, poza samym raportem.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Czytanie dokumentów (PDF, DOCX, XLSX…)

Agent czyta zwykły tekst od razu po instalacji. Do prawdziwych dokumentów — PDF, Word,
PowerPoint, Excel, HTML, CSV, EPUB — zainstaluj opcjonalne extra, a zyska narzędzie
`read_document`, które konwertuje dowolny z nich na Markdown:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Następnie skieruj zadanie na plik: *"Podsumuj report.pdf w 5 punktach."* Bez extra,
`read_document` zwraca jednoliniową wskazówkę instalacyjną zamiast się wywalić.

## Przeglądanie internetu (nawigacja, czytanie + działanie)

Narzędzie `browser` jest **wbudowane** — steruje prawdziwym Chromium (więc widzi strony
renderowane przez JavaScript, których zwykły `http_get` nie widzi). Playwright jest dostarczany
z Chimerą; binarka Chromium (~150MB) jest **pobierana automatycznie przy pierwszym użyciu
przeglądarki** (jednorazowy krok, którego pip nie może wykonać za ciebie). Bez wymaganego kroku
instalacji:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

Narzędzie `browser` ma następujące akcje:

- **`navigate` / `read`** — otwiera URL i wypisuje *interaktywne* elementy strony jako
  `[ref] role: name` (linki, przyciski, pola), więc agent klika/pisze po `ref`, nie po pikselu.
- **`read_text`** — **pełny wyrenderowany tekst** strony, do czytania/badania artykułu,
  dokumentu lub wyniku. Z extra `documents` to czysty **Markdown** (nagłówki, linki, listy
  zachowane dzięki MarkItDown); bez niego zwykły widoczny tekst. Podaj opcjonalny `url`, by
  otworzyć + przeczytać w jednym kroku.
- **`find`** — przeszukuje wyrenderowany tekst pod kątem zapytania i zwraca pasujące linie.
- **`click` / `type` / `back`** — steruje stroną po `ref`.

`CHIMERA_BROWSER_HEADLESS=false` uruchamia Chromium w trybie z widoczną głową (do debugowania).

Treść strony jest **niezaufana**: każdy wynik jest otoczony ogrodzeniem danych, a narzędzie
skaża przebieg, więc preferuj `solve --taint --guard` przy przeglądaniu i wyciągaj
ustrukturyzowane pola przez kwarantannowany czytnik zamiast działać na surowym tekście strony.
Bez extra `documents`, `read_text` nadal działa — po prostu jako zwykły tekst zamiast Markdown.

## Badanie tematu (wyszukiwanie + czytanie)

Połącz wyszukiwanie webowe z `read_text` przeglądarki, by zbadać coś i dostać brief ze źródłami
— `web_search` (wymaga `TAVILY_API_KEY`) znajduje strony, `browser read_text` czyta
każdą z nich (w tym strony bogate w JS), a `deliver` pisze brief:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Po gotową wersję z wykonywalnym sprawdzeniem na krok, zobacz
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief)
— używa od razu `arxiv_search` + `web_search`, a z zainstalowanym extra `browser` agent może
też `read_text` całe strony zamiast zatrzymywać się na fragmentach wyszukiwania.

## Scraping i bezpieczna ekstrakcja ustrukturyzowana

Dwa wbudowane narzędzia zamieniają dowolną stronę w czyste, gotowe dla LLM dane — bez extra do
instalacji:

- **`scrape`** — pobiera URL i zwraca czysty **Markdown + metadane**. Przechodzi przez kaskadę
  świadomą kosztów: najpierw zwykłe HTTP GET, eskalując do wbudowanej **przeglądarki**
  (renderowanie JS), jeśli strona wraca pusta, i — tylko jeśli ustawisz `FIRECRAWL_API_KEY` —
  spadając na **Firecrawl** dla ciężkich stron anty-bot. `render=http|browser|firecrawl` wymusza
  konkretny backend; `include_links` zwraca też linki strony.
- **`extract`** — wyciąga konkretne pola jako **zwalidowany JSON**, bezpiecznie. Podaj `url`
  (lub `content`) i listę `fields` (np. `["title", "price", "author"]`), a zwróci *tylko* te
  pola. Kluczowo, czyta stronę przez **kwarantannowany czytnik** Chimery — model bez narzędzi,
  którego wyjście jest walidowane wg schematu — więc **instrukcje ukryte na stronie nie mogą
  przejąć kontroli nad agentem**. To jest gwarancja bezpieczeństwa, której nie daje ci
  Firecrawl/ScrapeGraphAI: wroga strona może w najgorszym razie zwrócić złą wartość, nigdy nową
  instrukcję. Duże strony są dzielone na kawałki i scalane, kończąc wcześniej, gdy każde pole
  jest już wypełnione, by ograniczyć koszt. Dla **znanego szablonu strony**, podaj `selectors`
  (pole → CSS, np. `{"price": ".price", "link": "a.more::attr(href)"}`), a te pola są
  ekstrahowane **deterministycznie — za darmo, bez LLM** — z bezpiecznym LLM używanym tylko dla
  pól, których selektor nie wypełnił.

```bash
uv run chimera agent "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera agent "extract the fields title, price, availability from https://example.com/product --taint"
```

Dla całych stron są jeszcze dwa czasowniki:

- **`map`** — tanio wypisuje URL-e strony (czyta sitemapę, gdy jest, w przeciwnym razie skanuje
  linki strony). Opcjonalny filtr słowa kluczowego `search`. Uruchom to, by wyznaczyć zakres
  strony przed crawlowaniem jej.
- **`crawl`** — podąża za linkami z URL-a startowego i zwraca czysty Markdown każdej strony.
  Ograniczone przez `limit` i `max_depth`, domyślnie w obrębie tej samej domeny, i **świadome
  robots.txt** (przestrzega `Disallow` i `Crawl-delay`). `include`/`exclude` to wzorce glob URL.
  Długie crawle są **wznawialne**: granica przeszukiwania (frontier) jest zapisywana na dysku po
  każdej stronie, więc crawl przerwany na stronie N kontynuuje od N+1 przy kolejnym uruchomieniu
  (`resume=true` domyślnie).

```bash
uv run chimera agent "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Wszystko jest otoczone ogrodzeniem danych i skaża przebieg (to niezaufana treść webowa), więc
`solve --taint --guard` to bezpieczny sposób na działanie wobec niej. Opcjonalny fallback
Firecrawl jest używany *tylko* wtedy, gdy wbudowany silnik nie może pobrać strony, a klucz jest
ustawiony — Chimera scrapuje ogromną większość sieci sama, bez usługi zewnętrznej.

## Audio: mowa na tekst (transkrypcja)

Chimera potrafi zamieniać mowę na tekst — symetryczny partner jej narzędzi generowania obrazów
i tekstu na mowę. **Orkiestruje model Whisper** (nie trenuje go): narzędzie `transcribe_audio`
używa lokalnego **faster-whisper**, jeśli zainstalujesz extra `stt` (offline/prywatne), w
przeciwnym razie hostowanego API OpenAI Whisper (wymaga klucza OpenAI):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera agent "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Uwaga o zakresie, w uczciwym duchu tego projektu: Chimera jest **agentem**, nie modelem.
> Potrafi *używać* mowy na tekst, generowania obrazów, widzenia komputerowego czy klasycznego ML
> — wywołując API lub uruchamiając bibliotekę w swoim sandboksie kodu — ale nie (i nie potrafi
> sensownie) *reimplementować* Whispera, Stable Diffusion, PyTorcha ani OpenCV. Do data science /
> ML, sandbox `execute_code` już pozwala agentowi pisać i uruchamiać Pythona wobec scikit-learn,
> pandas, OpenCV itd. Orkiestracja mnoży możliwości agenta; reimplementacja dałaby tylko wolniejszą
> kopię.

## Pobierz wideo lub jego audio

Narzędzie `download_media` ściąga wideo (lub tylko jego audio) z YouTube i 1000+ innych stron do
workspace'u. Opakowuje **yt-dlp** (aktywnie utrzymywane, obsługuje zmienność szyfru/formatu/
bramek wiekowych, która topi scrapery jednej strony, jak pytube). Opcjonalne; ekstrakcja audio
wymaga też `ffmpeg` w PATH:

```bash
uv sync --extra media-dl
uv run chimera agent "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Naturalnie łączy się z `transcribe_audio` powyżej: pobierz → transkrybuj → podsumuj, wszystko w
jednym przebiegu.

## Analiza danych / ML (skill `data_analysis`)

Chimera nie reimplementuje scikit-learn — **pisze poprawny kod pandas/sklearn i uruchamia go**
w sandboksie `execute_code`. Skill `data_analysis` nazywa tę zdolność: daj mu zadanie i zbiór
danych, a wyprodukuje samodzielny skrypt (wczytaj → zbadaj → zamodeluj → oceń), który agent
następnie wykonuje.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera agent "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Generowanie obrazów (hostowane lub w pełni lokalne)

`generate_image` domyślnie używa API obrazów OpenAI. Do konfiguracji **offline / prywatnej**,
ustaw `CHIMERA_IMAGE_BACKEND=local` i zainstaluj (ciężkie, wymagające GPU) extra
`imagegen-local` — Chimera wtedy uruchamia **FLUX.1-schnell** (Apache-2.0) przez `diffusers`
lokalnie. `auto` (domyślne) używa lokalnego tylko wtedy, gdy nie ma klucza OpenAI.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera agent "generate an image of a fox in a snowy forest"
```

> Ten sam uczciwy zakres co powyżej: Chimera tutaj *uruchamia* model dyfuzyjny; nie trenuje go.
> Generowanie wideo (np. CogVideo) celowo **nie jest** wbudowane — to ciężki wytrenowany model,
> nie coś, co agent powinien nosić w swoim rdzeniu; sięgnij po hostowane API, jeśli kiedykolwiek
> tego potrzebujesz. Widzenie komputerowe (OpenCV) nie potrzebuje dedykowanego narzędzia — agent
> już robi `import cv2` w sandboksie kodu.

## Wykresy i wizualizacja danych

Dwa uzupełniające się sposoby na wykres — oba uczciwe co do zakresu (Chimera *używa* bibliotek
do wykresów; nie reimplementuje matplotlib/plotly/bokeh):

**1. Skill `data_visualization` — pisz kod wykresu, uruchom go w sandboksie.** Pokrywa
*wszystko* (niestandardowe/publikacyjne wykresy, 3D, cokolwiek): skill emituje samodzielny
skrypt używający matplotlib/seaborn (statyczny PNG/SVG) lub plotly (interaktywny HTML), z
wbudowaną dyscypliną backendu headless (`matplotlib.use("Agg")`) i zapisu do workspace'u.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera agent "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. Narzędzie `render_chart` — bezpieczna, deklaratywna specyfikacja Vega-Lite.**
Specyfikacja Vega-Lite to **martwe dane JSON, nie kod**: możliwe do zbadania,
kształtowane wg schematu, i ponownie renderowalne — mocniejsza historia governance niż
wykonywanie wygenerowanego kodu, dla standardowych wykresów, które pokrywa Vega-Lite (słupkowe/
liniowe/punktowe/histogram/heatmapa/fasetowane…). **Wyjście HTML nie wymaga extra** (osadza
specyfikację + CDN Vega); PNG/SVG używają opcjonalnego extra `viz-vega`
(`vl-convert-python`).

```bash
uv run chimera agent "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Uczciwy zakres: plotly opakowuje plotly.js, bokeh to w ~połowie TypeScript, renderer
> matplotlib to C++, a seaborn to cienka warstwa nad matplotlib — wszystko frameworki, które
> agent powinien *wywoływać*, nie przepisywać. Sandbox kodu już je importuje; skill po prostu
> nazywa zdolność i obsługuje pułapki headless. Vega-Lite jest wyjątkiem wartym dedykowanego
> narzędzia, ponieważ jego artefakt to bezpieczne dane deklaratywne.

## Zaplanuj dowolny z nich

Każdy przepis może działać na cronie i dostarczać do czatu:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

Zobacz [Wdrożenie](deploy.md) po bramę mesagingową i konfigurację 24/7.
