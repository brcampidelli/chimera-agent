---
source_sha256: f08c31cf980c0d86795fe456d5f9ed6871b58325c9e429e93f48c71b6e998356
---

# Рецепты

Настоящие, готовые к запуску сценарии, которые от начала до конца делают что-то полезное встроенными
инструментами. Любой запускается командой `chimera workflow <файл> -w <рабочая папка>`. Исходники
целиком лежат в каталоге
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples).

## Полностью локально, без ключа API (Ollama)

Запустите Chimera на модели, которая работает на вашей машине: ключа не нужно, ничего не покидает
компьютер. Установите [Ollama](https://ollama.com), скачайте модель и направьте на неё Chimera:

```bash
ollama pull llama3.1                     # or qwen2.5, mistral, phi3, …
export CHIMERA_MODEL=ollama/llama3.1     # the `ollama/` prefix = local, keyless
chimera run "Summarise this file in 3 bullets" -w .
```

Вот и всё — ни `OPENROUTER_API_KEY`, ни облака. Проверка учётных данных распознаёт `ollama/…` (и
`ollama_chat/…`) как локальную среду и пропускает. Если Ollama работает на другой машине, задайте
`CHIMERA_OLLAMA_BASE_URL=http://host:11434` (по умолчанию `http://localhost:11434`).

Локальные модели меньше, поэтому это *слабый* конец диапазона
[золотой середины](../bench/local_lift/RESULTS.md) — хорошо подходит для `chimera solve` (план и
«проверить или откатить» помогают слабой модели) и для приватности без сети, и хуже подходит для
разового рассуждения уровня передовых моделей. Смешивайте: локальная модель по умолчанию и облачные
`CHIMERA_FALLBACK_MODELS` для трудных вызовов.

## Разбор почты

Прочитать входящие, разложить на `URGENT / PERSONAL / NEWSLETTER / COLD-SALES` и написать сводку на
десять секунд чтения. Только чтение — ничего не удаляется, не переносится и не отправляется.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Нужны учётные данные IMAP. Настройка и ежедневное расписание:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Ежедневная исследовательская сводка

На входе тема, на выходе сводка из 5 пунктов со ссылками и выжимка в 3 строки (arxiv всегда;
веб-поиск, если задан ключ Tavily).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Наблюдение за репозиторием

Прогнать набор тестов репозитория и написать отчёт о состоянии, назвав все упавшие тесты. Только
чтение, кроме самого отчёта.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Чтение документов (PDF, DOCX, XLSX…)

Обычный текст агент читает сразу. Для настоящих документов — PDF, Word, PowerPoint, Excel, HTML, CSV,
EPUB — установите необязательное дополнение, и он получит инструмент `read_document`, превращающий
любой из них в Markdown:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Затем направьте задачу на файл: *«Изложи report.pdf в 5 пунктах»*. Без дополнения `read_document`
возвращает однострочную подсказку об установке, а не падает.

## Работа с вебом (переход, чтение и действия)

Инструмент `browser` **встроен** — он управляет настоящим Chromium, поэтому видит страницы,
отрисованные JavaScript, которых простой `http_get` не видит. Playwright идёт вместе с Chimera; сам
двоичный файл Chromium весом около 150 МБ **скачивается автоматически при первом использовании
браузера** (разовый шаг, который pip за вас сделать не может). Отдельной установки не требуется:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

У инструмента `browser` есть такие действия:

- **`navigate` / `read`** — открыть адрес и перечислить *интерактивные* элементы страницы как
  `[ref] role: name` (ссылки, кнопки, поля), чтобы агент нажимал и печатал по `ref`, а не по
  пикселям.
- **`read_text`** — **полный отрисованный текст** страницы, чтобы прочитать статью, документ или
  результат. С дополнением `documents` это чистый **Markdown** (заголовки, ссылки и списки
  сохраняются через MarkItDown); без него — обычный видимый текст. Необязательный `url` позволяет
  открыть и прочитать за один шаг.
- **`find`** — поискать запрос в отрисованном тексте и получить обратно совпавшие строки.
- **`click` / `type` / `back`** — управлять страницей по `ref`.

`CHIMERA_BROWSER_HEADLESS=false` запускает Chromium с окном, для отладки.

Содержимое страниц **недоверенное**: каждый результат обособляется как данные, а инструмент заражает
запуск, поэтому при работе с вебом предпочитайте `solve --taint --guard` и вытягивайте
структурированные поля через изолированный читатель, а не действуйте по сырому тексту страницы. Без
дополнения `documents` `read_text` всё равно работает — просто выдаёт обычный текст вместо Markdown.

## Исследование темы (поиск и чтение)

Соедините веб-поиск с `read_text` браузера, чтобы изучить вопрос и получить сводку со ссылками:
`web_search` (нужен `TAVILY_API_KEY`) находит страницы, `browser read_text` читает каждую
(в том числе тяжёлые на JavaScript), а `deliver` пишет сводку:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

Готовый вариант с исполняемой проверкой на каждом шаге смотрите в
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief)
— он сразу использует `arxiv_search` и `web_search`, а с установленным дополнением `browser` агент
может ещё и делать `read_text` целых страниц, а не останавливаться на выдержках из поиска.

## Сбор данных и безопасное структурное извлечение

Два встроенных инструмента превращают любую страницу в чистые данные, готовые для модели, и
устанавливать для этого ничего не нужно:

- **`scrape`** — забрать адрес и вернуть чистый **Markdown с метаданными**. Он идёт по каскаду,
  знающему цену: сначала обычный HTTP GET, с подъёмом к встроенному **браузеру** (отрисовка
  JavaScript), если страница вернулась пустой, и — только если вы задали `FIRECRAWL_API_KEY` — с
  откатом к **Firecrawl** для страниц с тяжёлой защитой от ботов. `render=http|browser|firecrawl`
  заставляет использовать конкретный движок; `include_links` дополнительно возвращает ссылки
  страницы.
- **`extract`** — безопасно вытащить нужные поля как **проверенный JSON**. Дайте ему `url` (или
  `content`) и список `fields` (например, `["title", "price", "author"]`), и он вернёт *только* эти
  поля. Важно, что он читает страницу через **изолированный читатель** Chimera — модель без
  инструментов, чей вывод проверяется по схеме, — поэтому **спрятанные в странице инструкции не могут
  перехватить агента**. Это та гарантия безопасности, которой Firecrawl и ScrapeGraphAI вам не дают:
  враждебная страница в худшем случае вернёт неверное значение, но никогда — новую инструкцию.
  Большие страницы режутся на части и сливаются, с досрочной остановкой, как только заполнены все
  поля, чтобы ограничить стоимость. Для **известного шаблона страницы** передайте `selectors` (поле →
  CSS, например `{"price": ".price", "link": "a.more::attr(href)"}`), и эти поля извлекутся
  **детерминированно — бесплатно, без модели**, а безопасная модель будет применена только к полям,
  которые селектор не заполнил.

```bash
uv run chimera run "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera run "extract the fields title, price, availability from https://example.com/product --taint"
```

Для целых сайтов есть ещё два действия:

- **`map`** — дёшево перечислить адреса сайта (читает карту сайта, если она есть, иначе просматривает
  ссылки страницы). Есть необязательный фильтр `search` по ключевому слову. Запускайте это, чтобы
  очертить сайт перед обходом.
- **`crawl`** — идти по ссылкам от начального адреса и возвращать чистый Markdown каждой страницы.
  Ограничивается `limit` и `max_depth`, по умолчанию не выходит за домен и **учитывает robots.txt**
  (соблюдает `Disallow` и `Crawl-delay`). `include` и `exclude` — это шаблоны адресов. Долгие обходы
  **возобновляемы**: очередь сохраняется на диск после каждой страницы, поэтому обход, прерванный на
  странице N, при следующем запуске продолжится с N+1 (`resume=true` по умолчанию).

```bash
uv run chimera run "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Всё обособляется как данные и заражает запуск (это недоверенное содержимое из веба), поэтому
безопасный способ действовать на его основе — `solve --taint --guard`. Необязательный откат к
Firecrawl используется *только* тогда, когда встроенный движок не смог забрать страницу и задан ключ:
подавляющую часть веба Chimera собирает сама, без внешних сервисов.

## Звук: речь в текст (расшифровка)

Chimera умеет превращать речь в текст — это симметричная пара к её генерации изображений и синтезу
речи. Она **управляет моделью Whisper** (а не обучает её): инструмент `transcribe_audio` использует
локальный **faster-whisper**, если вы поставите дополнение `stt` (без сети, приватно), иначе —
облачный Whisper API от OpenAI (нужен ключ OpenAI):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera run "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> Замечание об охвате, в честном духе этого проекта: Chimera — **агент**, а не модель. Она может
> *пользоваться* распознаванием речи, генерацией изображений, компьютерным зрением или классическим
> машинным обучением, вызывая API или запуская библиотеку в своей песочнице кода, — но она не
> *переписывает* Whisper, Stable Diffusion, PyTorch или OpenCV, да и не могла бы разумно этого
> делать. Для анализа данных и машинного обучения песочница `execute_code` уже позволяет агенту
> писать и запускать Python поверх scikit-learn, pandas, OpenCV и прочего. Оркестрация умножает
> агента; переписывание дало бы лишь копию помедленнее.

## Скачать видео или его звук

Инструмент `download_media` вытягивает видео (или только его звук) с YouTube и ещё тысячи с лишним
сайтов в рабочую папку. Он оборачивает **yt-dlp** (активно поддерживается и справляется с
чехардой шифров, форматов и возрастных ограничений, на которой ломаются односайтовые сборщики вроде
pytube). Включается по желанию; для извлечения звука нужен ещё `ffmpeg` в PATH:

```bash
uv sync --extra media-dl
uv run chimera run "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Естественно сочетается с `transcribe_audio` выше: скачать → расшифровать → изложить, всё за один
запуск.

## Анализ данных и машинное обучение (навык `data_analysis`)

Chimera не переписывает scikit-learn — она **пишет правильный код на pandas и sklearn и запускает
его** в песочнице `execute_code`. Навык `data_analysis` даёт этой способности имя: дайте ему задачу и
набор данных, и он выдаст самодостаточный скрипт (загрузить → изучить → построить модель → оценить),
который агент затем выполнит.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera run "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Генерация изображений (в облаке или полностью локально)

`generate_image` по умолчанию использует API изображений OpenAI. Для работы **без сети и приватно**
задайте `CHIMERA_IMAGE_BACKEND=local` и поставьте (тяжёлое, требующее GPU) дополнение
`imagegen-local` — тогда Chimera запускает **FLUX.1-schnell** (Apache-2.0) локально через
`diffusers`. Значение `auto` (по умолчанию) использует локальный путь только при отсутствии ключа
OpenAI.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera run "generate an image of a fox in a snowy forest"
```

> Тот же честный охват, что и выше: здесь Chimera *запускает* диффузионную модель, но не обучает её.
> Генерация видео (например, CogVideo) намеренно **не** встроена — это тяжёлая обученная модель, а не
> то, что агент должен носить в своей основе; если она вам понадобится, берите облачный API.
> Компьютерному зрению (OpenCV) отдельный инструмент не нужен — агент и так делает `import cv2` в
> песочнице кода.

## Графики и визуализация данных

Два взаимодополняющих способа построить график — оба честны относительно охвата (Chimera
*пользуется* библиотеками рисования, а не переписывает matplotlib, plotly или bokeh):

**1. Навык `data_visualization` — написать код графика и выполнить его в песочнице.** Покрывает
*всё* (свои и публикационные рисунки, трёхмерные, что угодно): навык выдаёт самодостаточный скрипт на
matplotlib и seaborn (статичный PNG или SVG) либо на plotly (интерактивный HTML), с уже встроенной
дисциплиной безоконного движка (`matplotlib.use("Agg")`) и сохранения в рабочую папку.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera run "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. Инструмент `render_chart` — безопасная декларативная спецификация Vega-Lite.** Спецификация
Vega-Lite — это **инертные данные JSON, а не код**: их можно осмотреть, они имеют форму схемы и их
можно отрисовать заново. Для стандартных графиков, которые покрывает Vega-Lite (столбики, линии,
точки, гистограммы, тепловые карты, панели), это более сильная история управления, чем выполнение
порождённого кода. **Для вывода в HTML дополнений не нужно** (спецификация встраивается вместе с CDN
Vega); PNG и SVG используют необязательное дополнение `viz-vega` (`vl-convert-python`).

```bash
uv run chimera run "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Честно об охвате: plotly оборачивает plotly.js, bokeh примерно наполовину написан на TypeScript,
> отрисовщик matplotlib — на C++, а seaborn — тонкий слой поверх matplotlib. Всё это каркасы, которые
> агенту следует *вызывать*, а не переписывать. Песочница кода уже их импортирует; навык лишь даёт
> способности имя и разбирается с подводными камнями безоконного режима. Vega-Lite — исключение,
> заслуживающее отдельного инструмента, потому что его артефакт — безопасные декларативные данные.

## Поставить любой из них на расписание

Каждый рецепт может работать по расписанию и доставлять результат в чат:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

О шлюзе мессенджеров и круглосуточной работе смотрите [Развёртывание](deploy.md).
