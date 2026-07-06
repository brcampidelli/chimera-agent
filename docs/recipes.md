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

## Schedule any of them

Every recipe can run on a cron and deliver to chat:

```bash
chimera cron add "morning brief" "0 7 * * *" "Research X; write a 5-bullet brief."
chimera serve   # runs jobs; with a bot configured, delivers to Discord/Telegram/Slack
```

See [Deploy](deploy.md) for the messaging gateway and 24/7 setup.
