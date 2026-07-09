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

For tasks that need the agent to *act on* or *read* a page — open it, read its content, click,
type — install the browser extra and it gains a `browser` tool that drives a real Chromium (so it
sees JavaScript-rendered pages the plain `http_get` can't):

```bash
uv sync --extra browser --extra documents   # or: pip install 'chimera-agent[browser,documents]'
playwright install chromium                  # one-time: fetch the browser binary
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
rather than acting on raw page text. Without the `browser` extra, `browser` returns an install hint;
without `documents`, `read_text` still works — just as plain text instead of Markdown.

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

## Schedule any of them

Every recipe can run on a cron and deliver to chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

See [Deploy](deploy.md) for the messaging gateway and 24/7 setup.
