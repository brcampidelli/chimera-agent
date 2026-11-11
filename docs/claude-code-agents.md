# Subagents written for Claude Code

Chimera reads subagent files in Claude Code's format (`name` and `description` in the frontmatter,
the body is the system prompt) from `.claude/agents` (up to the repository root), `~/.claude/agents`
and active plugins, beside its own `.chimera/agents`. The switch is `CHIMERA_CLAUDE_AGENTS` (on by
default; owner-only, the desktop bridge cannot write it). Code: `chimera/core/claude_agent_format.py`
and `chimera/core/markdown_agents.py`.

## A file only narrows

Whatever a file says is intersected with the tools the session already has, after governance,
posture, the denylist and reach. It cannot add a tool, raise a posture, mount an MCP server, run a
hook or pick a provider. `bypassPermissions`, `acceptEdits`, `auto` and `dontAsk` are ignored with a
visible note; `permissionMode: plan` leaves read-only tools. `model: opus|sonnet|haiku|fable` means a
role (weak, mid, top) that the owner maps in Settings; a repository that selects the top tier is
flagged on the Agents screen, because that is the owner's most expensive model.

## Agents without `tools` inherit the session (repositories included)

As in Claude Code, a Claude-format file with no `tools:` line gets every tool the session offers,
minus its `disallowedTools`. For a file from a repository this is not a widening: the set is what the
owner's session already allows, it stays behind Manual mode, the denylist, reach and taint, and the
body is fenced as untrusted project data. Write an explicit `tools:` to narrow further.

## Plugin agents without `tools` get none

This is deliberately different. A plugin agent that declares no tools runs with **no tools** (the
listing says "declares no tools; it runs with none"). A plugin is third-party code the owner switched
on, and Claude Code itself ignores `hooks`, `mcpServers` and `permissionMode` in plugin agents; here
the safe reading of "nothing declared" is "nothing granted". Add a `tools:` line to give one tools.

## `memory` grants write access to one directory

`memory: user | project | local` gives the agent Read/Write/Edit **inside its memory directory and
nowhere else** (`~/.claude/agent-memory/<name>/`, `.claude/agent-memory/<name>/`,
`.claude/agent-memory-local/<name>/`). That is a real grant: the agent can change files in your
checkout (project/local) or your home (user). What bounds it:

- `memory: user` works only for the owner's own files; a repository file asking for it is ignored
  ("ignorado: memória do usuário só para agentes do dono").
- The directory and every parent below its base (the workspace, or the owner's Claude folder) must be
  real folders. A symlink or Windows junction anywhere on the path disables the memory, checked at
  discovery and again at run time.
- The tools are the session's own governed tools behind a path fence, so **Manual mode still asks for
  a card before each write** (pinned by a test), and the denylist, reach and taint apply.
- The first 200 lines / 25 KB of `MEMORY.md` go into the prompt as fenced data, not instructions. When
  `CHIMERA_TRUST_WORKSPACE=0`, memory and agent text that came from the repository are also recorded
  on the taint ledger like an untrusted file.
- The Code turn's close-the-loop never stages `.claude/agent-memory*` paths.

## Limits

Discovery reads at most 200 files, three folders deep, and does not follow folder links. Parsed files
are cached by path, modification time and size, so a listing does not re-parse unchanged files.
