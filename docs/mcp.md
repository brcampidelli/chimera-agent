# Connecting MCP servers

MCP (Model Context Protocol) is the standard way to plug external tools into an agent —
GitHub, filesystems, Notion, databases, and hundreds more servers speak it. Chimera has a
first-class MCP client: any server's tools become ordinary Chimera tools, sitting in the
same registry as the built-ins, governed by the same allowlist/kernel/ledger layers.

## Install the client extra

The MCP client lives behind an optional extra so the core stays light:

```bash
uv sync --extra mcp
```

Most servers are Node packages, so you also need `npx` (ships with Node.js).

## 60-second smoke test (no credentials)

The reference filesystem server needs zero tokens — it just exposes read/write tools
over a directory you choose:

```python
from chimera.integrations import connect_stdio
from chimera.tools import default_registry

connector = connect_stdio(
    "fs",
    "npx", ["-y", "@modelcontextprotocol/server-filesystem", "./sandbox_dir"],
    name_prefix="fs_",   # avoid clashes with built-in tool names
)

registry = default_registry()
for tool in connector.tools():
    registry.register(tool)

print(registry.names())  # built-ins + fs_read_file, fs_write_file, fs_list_directory...
```

Hand that registry to an `Agent` (or see `examples/mcp_github.py` for the full loop) and
the model can now call the server's tools like any other.

## A real server: GitHub

```python
import os
from chimera.integrations import connect_stdio

connector = connect_stdio(
    "github",
    "npx", ["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_PERSONAL_ACCESS_TOKEN": os.environ["GITHUB_PERSONAL_ACCESS_TOKEN"]},
    name_prefix="gh_",
)
```

That's the whole integration: ~26 GitHub tools (search repos, read files, list issues,
create PRs, ...) appear in the registry. Runnable end-to-end version:
[`examples/mcp_github.py`](https://github.com/brcampidelli/chimera-agent/blob/main/examples/mcp_github.py).

## How it fits the safety layers

MCP tools are ordinary `Tool` objects, so everything composes:

- **Per-session allowlist** — `restrict_registry(registry, allow=["gh_search_repositories", ...])`
  grants only the MCP tools this run needs; un-granted ones never reach the model.
- **Governance kernel** — `govern_registry(...)` gates MCP calls allow/warn/review/block
  like any shell command.
- **Taint ledger** — wrap with `ledger_registry(...)` and MCP fetches are recorded; note
  that only tools named in `FETCH_TOOLS` are auto-classified today, so treat MCP content
  as untrusted and prefer running with `--taint --guard` semantics when the server pulls
  external data.

## Chimera *as* an MCP server

The client above lets Chimera call other tools. The reverse also works: run Chimera **as**
an MCP server so any MCP client — Claude Desktop, an IDE, another agent — can call the whole
engine as three tools.

```bash
uv sync --extra mcp
chimera serve --mcp        # speaks MCP over stdio
```

It exposes:

| Tool | What it does |
| --- | --- |
| `chimera_solve` | Autonomously solve a task with plan + verify-or-revert; returns the answer. |
| `chimera_fuse` | Answer a prompt through the LLM-Fusion engine (panel → judge → synthesizer). |
| `chimera_memory_search` | Search Chimera's long-term memory and return the top facts. |

Point an MCP client at it as a stdio server. For Claude Desktop, add to its config:

```json
{
  "mcpServers": {
    "chimera": { "command": "chimera", "args": ["serve", "--mcp"] }
  }
}
```

`--mcp` needs a provider key for `chimera_solve`/`chimera_fuse` (memory search works without
one). Add `--fuse` to route the solver's deep turns through fusion, `--no-memory` to skip
recall. Because stdio is the wire, all logs go to stderr — stdout carries only the protocol.

## Troubleshooting

- `TimeoutError: MCP server ... did not become ready` — the command didn't start. Run the
  same `npx ...` line manually in a terminal to see its error (missing token, missing
  Node, first-run package download being slow — bump `connect_timeout`).
- `ModuleNotFoundError: mcp` — install the extra: `uv sync --extra mcp`.
- Tool name clashes — always pass a `name_prefix`.
- The session runs the server as a subprocess for the life of your script; call
  `connector`'s session `close()` (or just let the process exit) to tear it down.
