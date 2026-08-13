# External agents (ACP)

Chimera can hand a coding turn to an agent it did not write — Claude Code, Gemini CLI, or any
adapter that speaks the [Agent Client Protocol](https://agentclientprotocol.com). The transcript,
the verifier, the checkpoint and the revert stay Chimera's; the work is somebody else's.

## Why

Chimera's claim is not that its loop is the only good loop. It is the governance around a loop: the
taint ledger, the write region, the snapshot before a turn, the verdict after it, the receipt that
says what actually happened. Those apply to any worker. Refusing to drive a worker you already trust
would mean insisting on the least interesting half of the product.

## What is guaranteed, and what is not

Read this part before the setup, because it is the part that decides whether this feature is right
for you.

An ACP agent declares which of the client's capabilities it will use, and Chimera offers
`fs/read_text_file` and `fs/write_text_file`. **Offering is not enforcing.** The agents worth driving
have file and shell tools of their own: Claude Code writes through the Claude Agent SDK, and it is
under no obligation to ask us first.

So, concretely:

| | Chimera's own loop | External agent |
|---|---|---|
| Write region refuses a write outside it | Always | Only for writes routed through us |
| Shell runs in the configured sandbox | Always | The agent runs commands its own way |
| Taint ledger arms the dangerous-tool gate | Always | Only for tools we mediate |
| Workspace snapshot before the turn | Yes | **Yes** |
| One-click revert of the whole turn | Yes | **Yes** |
| Every permission granted appears on the receipt | n/a | **Yes** |

The last three rows are the real guarantee, and they are what the posture line on the Code screen
promises when an external agent is selected. It stops saying "edits inside `/project`, runs no
commands" — that sentence describes tools Chimera owns — and says instead that a copy was taken and
the turn can be undone. A screen that kept the stronger sentence would be making a promise the turn
cannot keep.

Chimera also **declines** the ACP terminal capability. A terminal we hosted would be a second
execution path beside the sandbox with none of its rules.

## Setup

Nothing to configure for the agents Chimera knows:

```bash
npm i -g @agentclientprotocol/claude-agent-acp   # Claude Code, needs Node 22+
npm i -g @google/gemini-cli                       # Gemini CLI (its ACP mode is experimental upstream)
```

Then check what this machine can actually run:

```bash
chimera doctor
```

`external_agents` reports each one with `available: true/false` and, when false, the line that fixes
it. Availability is resolved on the machine the sidecar runs on — which for a packaged desktop build
is a machine assembled by CI that nobody looked at, so "it should be there" is not evidence.

The desktop app shows a **Worker** row above the composer listing whatever `doctor` found. When
nothing runnable is installed the row does not appear at all; `doctor` is where "you do not have this
yet, here is how" belongs.

## Credentials

Every child process Chimera spawns gets an environment with `API_KEY` / `TOKEN` / `SECRET` variables
stripped, so a shell command cannot echo a provider key. An ACP agent is a program whose entire job
needs one, so each agent declares the variables it needs **by name** and only those are put back:

- Claude Code: `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `CLAUDE_CONFIG_DIR`
- Gemini CLI: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`

Passing the whole environment would be easier and would hand every future adapter every key on the
machine.

## A custom adapter

Codex and others reach ACP through third-party adapters this project has not run. Rather than list an
unverified command — which turns "we did not check" into "supported" — point Chimera at the one you
have:

```jsonc
// POST /api/code/turn
{
  "message": "fix the failing test",
  "provider": "custom",
  "provider_command": "npx -y some-acp-adapter --flag"
}
```

The command is split shell-style and run **without** a shell, so a stray pipe is an argument rather
than a second command. On Windows, an argument containing cmd.exe syntax (`& | < > ^ %`) reaching a
`.cmd` launcher is refused rather than escaped: quoting rules differ per launcher and a wrong guess
runs your machine instead of a program on it.

## How it works

- One child process per **conversation**, not per turn. A `session/prompt` is one message in a
  context the agent is keeping; a fresh process each time would make every turn turn one.
- At most four live at once, and one untouched for an hour is closed. Each is a process holding a
  model connection.
- The process is started in its own group and killed as a tree — a coding agent is a launcher, and
  killing only the process we hold would leave the workers running and the workspace locked. An
  `atexit` reaper covers the app being quit mid-turn.
- The agent's `session/update` notifications are translated into the same events the native loop
  emits, so the screen needs no second implementation. Reasoning chunks are dropped rather than
  folded into the answer; a `diff` content block becomes the unified patch the transcript renders.
- Numbers the native loop owns and this one cannot report — `steps`, `context_peak_tokens` — arrive
  as `null` rather than `0`. Zero would read as "it did nothing".

## Limits

- Permission prompts are answered `allow_once` and **recorded on the receipt**. Gating a prompt the
  agent did not have to ask is theatre; the honest version is to grant, record, and rely on the
  checkpoint that also covers the writes which never asked.
- Fusion, roles, memory recall and the repo map are Chimera's own loop. An external turn reports
  `fused: false` and no memory usage because none of it happened.
- Gemini's ACP mode is flagged experimental upstream and its behaviour may change between releases.
