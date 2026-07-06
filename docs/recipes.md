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

## Browsing the web (navigate + act)

For tasks that need the agent to *act* on a page — open it, read it, click, type — install the
browser extra and it gains a `browser` tool that drives a real Chromium via the page's
accessibility tree (roles + names, not pixels):

```bash
uv sync --extra browser        # or: pip install 'chimera-agent[browser]'
playwright install chromium    # one-time: fetch the browser binary
```

Page content is **untrusted**: every result is data-fenced and the tool taints the run, so
prefer `solve --taint --guard` when browsing and pull structured fields through the quarantined
reader rather than acting on raw page text. Without the extra, `browser` returns an install hint.

## Schedule any of them

Every recipe can run on a cron and deliver to chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

See [Deploy](deploy.md) for the messaging gateway and 24/7 setup.
