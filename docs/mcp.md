# Connecting MCP servers

MCP (Model Context Protocol) is the standard way to plug external tools into an agent —
GitHub, filesystems, Notion, databases, and hundreds more servers speak it. Chimera has a
first-class MCP client: any server's tools become ordinary Chimera tools, sitting in the
same registry as the built-ins, governed by the same allowlist/kernel/ledger layers.

## Remote streamable HTTP

For an MCP endpoint that speaks streamable HTTP, configure `url` instead of `command`. The CLI
accepts `chimera mcp add NAME --url https://host.example/mcp`; authenticate with `--token-env
ENVIRONMENT_VARIABLE` to resolve a bearer token at runtime without saving its value in `mcp.json`.

For OAuth authorization-code + PKCE, configure `oauth_authorization_url`, `oauth_token_url`, and
`oauth_client_id` in `mcp.json` (or use their matching `chimera mcp add` options). Chimera opens the
authorization page, accepts the callback on loopback, exchanges the code with its PKCE verifier, and
stores the resulting token in the OS credential vault. The stored token is sent only as an
`Authorization: Bearer` header and is never logged. Install the optional `secrets` extra for OS vault
support; OAuth setup fails closed if no vault is available.

The sign-in runs only from an explicit Test (`chimera mcp test NAME` or the screen's Test button).
At boot, the pool and autoload never open a browser: a server with no stored token is skipped and
the log says to run the Test. A credential (bearer token or OAuth exchange) is only ever sent over
https, or over plain http to loopback; a remote `http://` URL with a token is refused.

Remote servers pass through the same configured MCP tool interface, registry namespace, long-lived
pool, probe command, error handling and observation fence as stdio servers. Treat remote tool
metadata and results as untrusted server content.

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
- **Server `instructions`** — the text a server returns from `initialize` is dropped, by decision:
  it is untrusted server text, and nothing marks it as data the way a fenced fetch is marked. The
  cost is that a server's usage guidance never reaches the model; a host that does pass it on should
  not count on it either (arXiv 2608.08467: with a search tool available, 9 of 24 models fell below
  15% on lookups placed in server instructions). Passing it as fenced data under taint is open, not
  done. Dropping it is **not** a boundary against server-written text: the same server's tool names
  and descriptions reach the model as the server wrote them, unfenced, so a server you connect is a
  server whose words the model reads.
- **Manifest pinning** — the first time a server is mounted, its tool names, descriptions and input
  schemas are remembered in `mcp_pins.json` beside `mcp.json`. If a later mount lists anything
  different, the server is **held**: the app, `chimera serve` and its bots (everything that mounts
  through the shared MCP pool) leave it unmounted until you approve the change, with the old and new
  text shown, through `chimera mcp approve NAME` or the MCP screen. `chimera mcp list` names held
  servers. The mounted tools are the listing that was checked, so a server cannot answer the check
  with one text and the model with another. Adding or removing the server through `chimera mcp
  add/remove` or the app forgets its pin (the next mount is first sight again). Pinning is trust on
  first use: it catches a description that **changes**, not one that was hostile from the start.
  **Not gated:** the Python API above. `connect_stdio` mounts whatever the server lists, and so does
  `autoload_into_registry` unless you pass it `mcp_path`, the store whose pins it should check.
- **Selection cues** — `chimera mcp test` and the MCP screen annotate tools with phrases that try to
  steer which tool the model picks ("always use this tool", "do not use other tools", "ignore
  previous instructions", `<IMPORTANT>`), read over the description and every parameter description.
  An annotation only: it refuses nothing, and how often it fires on honest servers has not been
  measured.

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

## Letting Claude operate the desktop app

`chimera serve --mcp` builds its own agent. `chimera mcp desktop` builds nothing: it is a
remote control for the desktop app **you already have open**, so Claude sees the same
conversations, runs and approvals you do, and whatever it starts runs under the app's
governance, on the app's screens.

1. In the app, open **Settings → Claude** and turn on **Allow Claude to operate this app**.
2. Register the server with Claude Code (or add the same command to Claude Desktop's config):

   ```bash
   claude mcp add chimera-desktop -- chimera mcp desktop
   ```

With the first switch on, Claude can read and start conversations (`desktop_send`), runs,
batches, boards and cron jobs, search and edit memory, and read files and git state. A run
it starts carries the posture you configured, and a request that tries to widen it — a
`verify` command, host execution, another agent, auto-approval — is refused. Approvals
stay with you: `desktop_approvals` lists them and nothing more. When a turn stops for one,
`desktop_send` returns at once saying it is waiting for you, and the turn carries on in the
app; `desktop_job` reports how it ends.

The second switch, **Full control**, adds `desktop_approve` (answer approvals and gated
steps) and `desktop_settings` (edit settings and the agent's identity). With it on, Claude can
approve actions without you — and a prompt-injected page or message the agent reads could lead
it to. Those two tools are not listed at all while it is off, and the app refuses them if
called anyway.

Some decisions stay yours whichever switch is on. Which model answers — every model setting,
the fallback chain, the fusion panel, judge and synthesizer, the cost mode, the cascade and
verified answers — and whether the app runs scheduled jobs, Claude can only *suggest*: nothing
is written, and the app shows you a card with each setting's value now and the value proposed,
to approve or refuse there. Claude cannot answer that card, by any route; if the setting changed
before you approve, nothing is applied. And granting a folder its commands, running a command in
the Runner, starting a messaging bot and saving an agent with its tool grants are refused
through the bridge altogether: you do them in the app.

And a run Claude starts, under either switch, runs on the models you configured and reaches no
further than the posture you configured. A request that names a model, a role plan, a profile, a
fusion panel or another agent is refused; so is a wider posture — more reach, looser approvals,
host execution or a `verify` command where you granted no shell, or auto-approval. Asking a run to
do less (read only, or approvals always) is allowed.

What neither switch allows: reading or writing an API key, token or webhook. Settings
edits refuse credential names, the routes that carry keys or share links are not reachable,
credential files (`.env`, private keys) cannot be read, written or searched, and every
result is scrubbed of credential values. Nor can a call point a workspace at the app's own
data folder, or at a folder that contains it (your home directory, for instance): that is
where approval answers are kept, and a file written there would answer one.

How it connects: while the switch is on, the app writes `~/.chimera/desktop-bridge.json`
(the URL of its loopback API and a random token; on POSIX readable only by you, on Windows
inside your profile). Turning the switch off or closing the app deletes it and retires the
token. If the app is closed, every tool answers "Chimera desktop is not running, or 'Allow
Claude to operate this app' is off in Settings." Claude lists tools when it connects, so
after turning **Full control** on or off, reconnect the server (`/mcp` in Claude Code) to
see the new list.

## Speaking A2A (agent → agent)

MCP connects agents to *tools*; **A2A** (Agent2Agent, Linux Foundation) connects agents to
*each other* — it's native in LangGraph, CrewAI, and AutoGen. Chimera speaks it too, so a
LangGraph/CrewAI orchestrator can delegate a task to Chimera and get a completed result back.

```bash
chimera a2a-card                       # print the Agent Card JSON
chimera serve --a2a                    # HTTP gateway + A2A endpoint
```

`serve --a2a` adds two routes to the HTTP server:

| Route | Purpose |
| --- | --- |
| `GET /.well-known/agent.json` | The Agent Card — identity + advertised skills (solve, fuse). |
| `POST /a2a` | JSON-RPC 2.0 task lifecycle: `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`. |

A client sends `message/send` with a text part; Chimera runs the autonomous agent and returns
a `completed` (or `failed`) task carrying the answer as an agent message. Or it sends
`message/stream` and gets a **Server-Sent Events** stream: the task in `working` state first,
then the `completed`/`failed` task once the run finishes — so an orchestrator sees progress
without polling. The agent card advertises `capabilities.streaming: true`.

**Scope, honestly:** the stream currently emits two events (working → final), not per-step
token deltas, and push notifications aren't implemented. That's a conformant, pollable-free
stream — enough to be a first-class streamable node in a LangGraph/CrewAI app.

## Troubleshooting

- `TimeoutError: MCP server ... did not become ready` — the command didn't start. Run the
  same `npx ...` line manually in a terminal to see its error (missing token, missing
  Node, first-run package download being slow — bump `connect_timeout`).
- `ModuleNotFoundError: mcp` — install the extra: `uv sync --extra mcp`.
- Tool name clashes — always pass a `name_prefix`.
- The session runs the server as a subprocess for the life of your script; call
  `connector`'s session `close()` (or just let the process exit) to tear it down.
