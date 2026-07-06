# Chimera

An open-source (Apache-2.0), self-evolving AI agent whose reasoning core **fuses several
models** (panel → judge → synthesizer) behind a cost-aware router — with a governance
kernel, a sandbox, and a memory that learns.

This site is task-oriented: pick what you want to do.

<div class="grid cards" markdown>

- **:material-rocket-launch: Get started**
  Install, add a key, run your first task in five minutes.
  [Install & first run →](usage.md)

- **:material-toolbox: Do something real**
  Runnable recipes: email triage, a daily research brief, a repo watchdog.
  [Recipes →](recipes.md)

- **:material-power-plug: Connect tools**
  Plug in any MCP server (GitHub, filesystem, …).
  [MCP servers →](mcp.md)

- **:material-server: Operate it**
  Run 24/7 on a small server; schedule jobs; deliver to chat.
  [Deploy →](deploy.md)

- **:material-shield-lock: Security**
  Governance, sandbox, taint tracking — and their honest limits.
  [Security →](security.md)

- **:material-sitemap: Understand it**
  How the fusion core, evolution, and safety layers fit together.
  [Architecture →](architecture.md)

</div>

## The one-liner

```bash
uv sync --extra dev && uv run chimera init
```

Then try `chimera run "..."`, or a real recipe:

```bash
chimera workflow examples/email_triage/triage.yaml -w ./triage_workspace
```

## Honest by default

Chimera is **alpha**. It ships defense-in-depth, but the docs say plainly where each
safeguard stops — the injection defenses even publish a measured number
(`chimera redteam`). See [Security](security.md).
