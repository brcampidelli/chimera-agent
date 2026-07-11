# Recipes

Real, runnable workflows that do something useful end-to-end with the built-in tools. Run
any with `chimera workflow <file> -w <workspace>`. Full sources live in the
[`examples/`](https://github.com/brcampidelli/chimera-agent/tree/main/examples) folder.

## Email triage

Read your inbox, classify `URGENT / PERSONAL / NEWSLETTER / COLD-SALES`, write a
ten-second digest. Read-only — nothing deleted, moved or sent.

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

Needs IMAP credentials. Setup + daily scheduling:
[examples/email_triage/README.md](https://github.com/brcampidelli/chimera-agent/blob/main/examples/email_triage/README.md).

## Daily research brief

A topic in, a 5-point sourced brief + 3-line digest out (arxiv always; web search if a
Tavily key is set).

```bash
chimera workflow examples/research_brief/brief.yaml -w ./brief_workspace
```

## Repo watchdog

Run a repo's test suite and write a health report naming any failing tests. Read-only
except the report.

```bash
chimera workflow examples/repo_watchdog/watch.yaml -w /path/to/your/repo
```

## Reading documents (PDF, DOCX, XLSX…)

The agent reads plain text out of the box. For real documents — PDF, Word, PowerPoint, Excel,
HTML, CSV, EPUB — install the optional extra and it gains a `read_document` tool that converts
any of them to Markdown:

```bash
uv sync --extra documents      # or: pip install 'chimera-agent[documents]'
```

Then point a task at a file: *"Summarize report.pdf into 5 bullets."* Without the extra,
`read_document` returns a one-line install hint instead of failing.

## Browsing the web (navigate, read + act)

The `browser` tool is **built in** — it drives a real Chromium (so it sees JavaScript-rendered pages
the plain `http_get` can't). Playwright ships with Chimera; the ~150MB Chromium binary is **downloaded
automatically the first time you use the browser** (a one-time step pip can't do for you). No install
step required:

```bash
# nothing to install — just use it. To turn the auto-download off and fetch it yourself:
#   CHIMERA_BROWSER_AUTO_INSTALL=0  +  playwright install chromium
# For clean Markdown out of read_text (instead of plain text), also add the documents extra:
uv sync --extra documents        # or: pip install 'chimera-agent[documents]'
```

The `browser` tool has these actions:

- **`navigate` / `read`** — open a URL and list the page's *interactive* elements as `[ref] role: name`
  (links, buttons, fields), so the agent clicks/types by `ref`, not by pixel.
- **`read_text`** — the page's **full rendered text**, for reading/researching an article, doc or
  result. With the `documents` extra it's clean **Markdown** (headings, links, lists preserved via
  MarkItDown); without it, the plain visible text. Pass an optional `url` to open + read in one step.
- **`find`** — search the rendered text for a query and get back the matching lines.
- **`click` / `type` / `back`** — drive the page by `ref`.

`CHIMERA_BROWSER_HEADLESS=false` runs Chromium headful for debugging.

Page content is **untrusted**: every result is data-fenced and the tool taints the run, so prefer
`solve --taint --guard` when browsing and pull structured fields through the quarantined reader
rather than acting on raw page text. Without the `documents` extra, `read_text` still works — just as
plain text instead of Markdown.

## Researching a topic (search + read)

Combine web search with the browser's `read_text` to research something and get a sourced brief —
`web_search` (needs `CHIMERA_TAVILY_API_KEY`) finds the pages, `browser read_text` reads each one
(including JS-heavy sites), and `deliver` writes the brief:

```bash
uv run chimera solve "Research 'on-device small language models 2026': web_search for sources, \
  open the top 3 with the browser and read_text each, then write a 5-bullet sourced brief to brief.md" \
  --taint --verify "test -s brief.md"
```

For a ready-made version with an executable check per step, see
[`examples/research_brief`](https://github.com/brcampidelli/chimera-agent/tree/main/examples/research_brief) — it uses `arxiv_search` +
`web_search` out of the box, and with the `browser` extra installed the agent can also `read_text`
full pages instead of stopping at search snippets.

## Scraping & safe structured extraction

Two built-in tools turn any page into clean, LLM-ready data — no extra to install:

- **`scrape`** — fetch a URL and return clean **Markdown + metadata**. It walks a cost-aware cascade:
  a plain HTTP GET first, escalating to the built-in **browser** (JS render) if the page comes back
  empty, and — only if you set `FIRECRAWL_API_KEY` — falling back to **Firecrawl** for heavy anti-bot
  pages. `render=http|browser|firecrawl` forces a specific backend; `include_links` also returns the
  page's links.
- **`extract`** — pull specific fields as **validated JSON**, safely. Give it a `url` (or `content`)
  and a list of `fields` (e.g. `["title", "price", "author"]`) and it returns *only* those fields.
  Crucially, it reads the page through Chimera's **quarantined reader** — a tool-less model whose
  output is schema-validated — so **instructions hidden in the page can't hijack the agent**. That is
  the safety guarantee Firecrawl/ScrapeGraphAI don't give you: a hostile page can at worst return a
  wrong value, never a new instruction. Large pages are chunked and merged, stopping early once every
  field is filled to cap cost. For a **known page template**, pass `selectors` (field → CSS, e.g.
  `{"price": ".price", "link": "a.more::attr(href)"}`) and those fields are extracted **deterministically
  — free, no LLM** — with the safe LLM used only for fields a selector didn't fill.

```bash
uv run chimera run "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera run "extract the fields title, price, availability from https://example.com/product --taint"
```

For whole sites there are two more verbs:

- **`map`** — list a site's URLs cheaply (reads the sitemap when there is one, else scans the page's
  links). Optional `search` keyword filter. Run this to scope a site before crawling it.
- **`crawl`** — follow links from a seed URL and return each page's clean Markdown. Bounded by `limit`
  and `max_depth`, same-domain by default, and **robots.txt-aware** (it obeys `Disallow` and
  `Crawl-delay`). `include`/`exclude` are URL glob patterns. Long crawls are **resumable**: the frontier
  is checkpointed to disk after every page, so a crawl interrupted at page N continues from N+1 on the
  next run (`resume=true` by default).

```bash
uv run chimera run "map https://docs.example.com then crawl the /guide section (max 20 pages) and summarize it"
```

Everything is data-fenced and taints the run (it's untrusted web content), so `solve --taint --guard`
is the safe way to act on it. The optional Firecrawl fallback is used *only* when the built-in engine
can't fetch a page and the key is set — Chimera scrapes the great majority of the web itself, with no
external service.

## Audio: speech-to-text (transcribe)

Chimera can turn speech into text — the symmetric partner to its image-generation and text-to-speech
tools. It **orchestrates a Whisper model** (it doesn't train one): the `transcribe_audio` tool uses
local **faster-whisper** if you install the `stt` extra (offline/private), otherwise the hosted OpenAI
Whisper API (needs an OpenAI key):

```bash
uv sync --extra stt      # optional: local, offline transcription (heavier — downloads a model)
uv run chimera run "transcribe meeting.m4a and give me 5 bullet-point action items"
```

> A note on scope, in the honest spirit of this project: Chimera is an **agent**, not a model. It can
> *use* speech-to-text, image generation, computer vision, or classic ML — by calling an API or running
> a library in its code sandbox — but it does not (and cannot sensibly) *reimplement* Whisper, Stable
> Diffusion, PyTorch, or OpenCV. For data science / ML, the `execute_code` sandbox already lets the
> agent write and run Python against scikit-learn, pandas, OpenCV, etc. Orchestration multiplies the
> agent; reimplementation would only produce a slower copy.

## Download a video or its audio

The `download_media` tool pulls a video (or just its audio) from YouTube and 1000+ other sites into
the workspace. It wraps **yt-dlp** (actively maintained, handles the cipher/format/age-gate churn that
sinks single-site scrapers like pytube). Opt-in; audio extraction also needs `ffmpeg` on PATH:

```bash
uv sync --extra media-dl
uv run chimera run "download the audio of https://youtu.be/… then transcribe it and summarize"
```

Pairs naturally with `transcribe_audio` above: download → transcribe → summarize, all in one run.

## Data analysis / ML (the `data_analysis` skill)

Chimera doesn't reimplement scikit-learn — it **writes correct pandas/sklearn code and runs it** in the
`execute_code` sandbox. The `data_analysis` skill names that capability: give it a task and a dataset
and it emits a self-contained script (load → explore → model → evaluate) the agent then executes.

```bash
uv sync --extra data     # pandas + scikit-learn for the generated code
uv run chimera run "use the data_analysis skill: predict churn from customers.csv and report accuracy"
```

## Image generation (hosted or fully local)

`generate_image` uses the OpenAI image API by default. For an **offline / private** setup, set
`CHIMERA_IMAGE_BACKEND=local` and install the (heavy, GPU-bound) `imagegen-local` extra — Chimera then
runs **FLUX.1-schnell** (Apache-2.0) via `diffusers` locally. `auto` (the default) uses local only when
no OpenAI key is present.

```bash
uv sync --extra imagegen-local     # pulls torch + diffusers; downloads multi-GB weights on first use
CHIMERA_IMAGE_BACKEND=local uv run chimera run "generate an image of a fox in a snowy forest"
```

> Same honest scope as above: Chimera *runs* a diffusion model here; it does not train one. Video
> generation (e.g. CogVideo) is deliberately **not** built in — it's a heavyweight trained model, not
> something an agent should carry in its base; reach for a hosted API if you ever need it. Computer
> vision (OpenCV) needs no dedicated tool — the agent already does `import cv2` in the code sandbox.

## Charts & data visualization

Two complementary ways to make a chart — both honest about scope (Chimera *uses* plotting libraries; it
doesn't reimplement matplotlib/plotly/bokeh):

**1. The `data_visualization` skill — write chart code, run it in the sandbox.** Covers *everything*
(custom/publication figures, 3D, anything): the skill emits a self-contained script using
matplotlib/seaborn (static PNG/SVG) or plotly (interactive HTML), with the headless backend
(`matplotlib.use("Agg")`) and save-to-workspace discipline baked in.

```bash
uv sync --extra viz     # matplotlib + seaborn + plotly for the generated code
uv run chimera run "use data_visualization: line chart of revenue.csv over time, save revenue.png"
```

**2. The `render_chart` tool — a safe, declarative Vega-Lite spec.** A Vega-Lite spec is **inert JSON
data, not code**: inspectable, schema-shaped, and re-renderable — a stronger governance story than
executing generated code, for the standard charts Vega-Lite covers (bar/line/scatter/histogram/
heatmap/faceted…). **HTML output needs no extra** (it embeds the spec + the Vega CDN); PNG/SVG use the
optional `viz-vega` extra (`vl-convert-python`).

```bash
uv run chimera run "build a Vega-Lite bar chart of {A:5,B:8,C:3} and render_chart it to chart.html"
uv sync --extra viz-vega   # optional: static PNG/SVG rendering (heavy — Rust+V8 binary)
```

> Honest scope: plotly wraps plotly.js, bokeh is ~half TypeScript, matplotlib's renderer is C++, and
> seaborn is a thin layer over matplotlib — all frameworks an agent should *call*, not rewrite. The
> code sandbox already imports them; the skill just names the capability and handles the headless
> gotchas. Vega-Lite is the exception worth a dedicated tool because its artifact is safe declarative
> data.

## Schedule any of them

Every recipe can run on a cron and deliver to chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

See [Deploy](deploy.md) for the messaging gateway and 24/7 setup.
