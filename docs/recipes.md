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
[`examples/research_brief`](../examples/research_brief/README.md) — it uses `arxiv_search` +
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
  field is filled to cap cost.

```bash
uv run chimera run "scrape https://news.ycombinator.com and summarize the top 5 stories"
uv run chimera run "extract the fields title, price, availability from https://example.com/product --taint"
```

Everything is data-fenced and taints the run (it's untrusted web content), so `solve --taint --guard`
is the safe way to act on it. The optional Firecrawl fallback is used *only* when the built-in engine
can't fetch a page and the key is set — Chimera scrapes the great majority of the web itself, with no
external service.

> Roadmap: `map` (list a site's URLs) and `crawl` (follow links across a whole site, robots-aware and
> resumable) are the next phase on top of this foundation.

## Schedule any of them

Every recipe can run on a cron and deliver to chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

See [Deploy](deploy.md) for the messaging gateway and 24/7 setup.
