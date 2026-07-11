# Examples

Real, runnable Chimera workflows — each does something useful end-to-end with the tools
that ship in the box. Run any of them with `chimera workflow <file> -w <workspace>`.

| Example | What it does | Needs |
|---|---|---|
| [project_demo](project_demo/) | **Run a whole project against a spec** — Chimera builds a module to completion, accepts it only when the Spec is satisfied, and pauses before risky steps. The flagship autonomous feature. | a model key |
| [email_triage](email_triage/) | Read your inbox, classify URGENT / PERSONAL / NEWSLETTER / COLD-SALES, write a 10-second digest. Read-only. | IMAP creds |
| [research_brief](research_brief/) | Turn a topic into a 5-point sourced brief + a 3-line digest. | a model key (arxiv is free; web search optional) |
| [repo_watchdog](repo_watchdog/) | Run a repo's test suite and write a health report naming any failing tests. | a model key |
| [mcp_github.py](mcp_github.py) | Wire a real MCP server (GitHub) into the agent loop. | `uv sync --extra mcp`, a GitHub token |
| [workflow.yaml](workflow.yaml) | A minimal build-and-report loop (solve → run). | a model key |

New here? Run `chimera init` first (sets up your key), then try `email_triage` — it's the
most immediately useful. Every recipe can be scheduled with `chimera cron` and delivered to
chat with the messaging gateway (see [docs/deploy.md](../docs/deploy.md)).

Honest note: these run with a free model, but a stronger model gives sharper results. Each
step is gated by an executable check, so a silent failure is retried, not passed off.
