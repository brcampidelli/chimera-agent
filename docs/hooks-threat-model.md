# Lifecycle hooks: the threat model, written before the code

**Status.** Owner decision of 2026-10-05: lifecycle hooks become the owner's choice, one on/off
setting, **off by default**. Until that decision the channel was closed on purpose —
[`docs/audits/sleeper-channels.md`](audits/sleeper-channels.md) row 13 recorded "typed callables and
hard-coded tables only" as a security property, not as a missing feature. This document is the
condition the decision was made under: it names what a hook can be used for by an attacker, and
the mechanism that contains each one. The implementation is held to it by tests named in the last
section; where a containment is partial, the residual is said here rather than discovered later.

## Why the channel was closed

arXiv 2609.03884 compromised all seven agent harnesses it evaluated, up to 92.5%, with the
defender at 0% recall, through hooks that bind **shell commands** to runtime events
([`bench/PLAN-study16-eight-axes.md`](../bench/PLAN-study16-eight-axes.md) §6). The classes below
are this project's decomposition of that shape into the edges Chimera would expose. They are not a
transcription of the paper's own taxonomy, which was not re-read for this document; what is taken
from it is the shape — a string written somewhere, bound to an event, executed later with the
harness's authority, and invisible to whoever was watching.

## What a hook is here

A hook is an entry in **one file the owner writes by hand**, `<CHIMERA_HOME>/chimera-hooks.json`,
read only when `CHIMERA_HOOKS=true`. Two events, two kinds:

| | `pre_tool` (before the tool runs) | `post_tool` (after it ran) |
|---|---|---|
| **static** (`decision`, written in the file) | deny, ask, or annotate | deny (withhold the output) or annotate |
| **shell** (`command`, run in the sandbox) | its JSON answer: deny, ask, annotate, or nothing | its JSON answer: deny (withhold) or annotate |

A hook answers with **at most** one of `deny`, `ask`, `annotate`. There is no `allow`. The strictest
answer among the hooks that matched wins (`deny` > `ask` > `annotate` > nothing), and whatever the
hooks answer, the call then meets the trust kernel and the taint ledger exactly as it would have
without them.

## The attack classes, and how each is contained

### A1. The agent installs or edits a hook (persistence)

The paper's shape at its most dangerous: injected text gets the agent to write a hook, and the hook
fires on every later call, with no injection needed any more. This is the "hook-update trust path"
that study 16 asked to audit.

**Contained by:**
- The file lives inside Chimera's data folder, which every agent write tool, every app file route
  and the external-agent (ACP) turn already refuse by file identity
  (`chimera/core/own_files.py:protected_reason`, `chimera/tools/workspace.py:refuse_own_files`).
- The three tools that run arbitrary text — `run_shell`, `execute_code`, `code_interpreter` — refuse
  a command or program that names the hooks file, by name or by any path that resolves to it
  (`chimera/core/queue_fence.py:reaches_hooks_file`, called from `queue_refusal`). The same reading
  as the approval-queue fence: quoting removed, variables expanded, file identity compared.
- The switch itself, `CHIMERA_HOOKS`, and its host-execution companion are owner-only: the desktop
  bridge refuses to write either one, full control or not (`chimera/api/bridge_routes.py`,
  `GUARD_SETTINGS`), and Chimera's own `.env`, where a saved value lands, is refused to the write
  tools like the data folder.
- No API route writes the hooks file. The owner edits it with an editor.

**Residual, said once.** The shell fence reads what a command says, not what it computes:
`queue_fence.py` lists the constructions it does not see (a path assembled at run time, brace
expansion, command substitution). On a host with no OS sandbox and `CHIMERA_HOST_EXEC=allow`, the
agent's shell runs as the owner and could write the file through one of those. That is the same
residual the approval queue has, and the same answer applies: the hook-update path is narrowed, not
closed, wherever the owner has also let the agent's shell run unconfirmed on the host. Every hook
invocation's receipt carries the SHA-256 of the file it was read from, so a change the owner did not
make is visible in the audit.

### A2. A hook runs a shell command on the host

The paper's execution edge: the hook's command runs with the harness's privileges.

**Contained by:** a shell hook runs through `get_sandbox()` — the same sandbox the agent's own shell
uses (OS kernel sandbox with network off and writes confined to the working directory, or the
Docker sandbox). Where the sandbox does not genuinely isolate (`sandbox_is_isolated` is False:
Windows, a Linux without bubblewrap, Docker configured with the daemon down), the hook **is refused
and the tool call is refused with it**, unless the owner has set `CHIMERA_HOOKS_HOST_EXEC=true` —
a second, separate owner-only switch that says "I accept my hooks running on my machine". Refusing
the tool call rather than skipping the hook is deliberate: a hook the owner configured and that
could not run is a guard that is missing, and silently going ahead is the widening this design
exists to rule out.

### A3. A hook widens what the agent may do

The quietest class: a hook answers "allow", "approve", "bypass", or rewrites the call's arguments,
and a guard that would have stopped the call no longer does.

**Contained by construction, not by filtering.** The composition of hook answers has no value that
widens: `deny`, `ask`, `annotate` and nothing. A hook's answer is read by name from a fixed set; any
other decision (`allow`, `approve`, `continue`, …) and any other key (`arguments`,
`updated_input`, `permission`, …) is ignored, and the receipt lists what was ignored under
`refused`. The arguments the tool runs with are the ones the model sent: the wrapper never passes
anything a hook produced to the tool. An `ask` a person approves releases only the hook's own
question — the call still meets the kernel, the denylist and the taint ledger after it. Hooks sit
*inside* the taint ledger and *outside* the kernel, so a hook can stop a call those layers would
let through, and nothing a hook says reaches either of them.

### A4. Hook output steers the model (injection through the hook)

A shell hook's output is computed from data — the workspace, a fetched file, a tool's arguments —
so it can carry instructions.

**Contained by:** what a shell hook says that reaches the model (the reason of a `deny`, the note
of an `annotate`) is passed through `sanitize_untrusted` and wrapped in the data fence, and the run's
taint ledger records it as a fetch (`hook:<id>`), so the run is tainted from that point and the
narrowing that follows untrusted input applies. Text written in a static hook is the owner's own
words in the owner's own file, and is fenced but does not taint.

### A5. The event payload injects into the hook's command

A hook command that interpolates the tool's arguments (`echo {command}`) is a command-injection
point for whatever the model chose to send.

**Contained by:** nothing is ever interpolated into a hook's command. The command runs exactly as
the owner wrote it; the event — event name, tool, arguments with document bodies elided — is
written to `event.json` in a fresh, empty working directory, and the hook reads it from there.

### A6. A hook exfiltrates

A hook sees every tool call; a hook with a network is a tap.

**Contained by:** the sandbox's network. The OS sandbox runs with the network off; the Docker
sandbox follows `CHIMERA_SANDBOX_NETWORK` (owner-only, `none` by default). On the host — only after
the owner set `CHIMERA_HOOKS_HOST_EXEC=true` — there is no network boundary, which the setting's
hint says. Document bodies (`content`, `code`, `patch`, …) are elided from the event before the
hook sees them (`governed_tool.elide_values`), so a hook does not receive a file's contents or a
program's source to begin with.

### A7. Hooks act unseen

The paper's defender had 0% recall. A guard nobody can see fire cannot be audited, and a hook
somebody slipped in cannot be noticed.

**Contained by:** every invocation writes a receipt — a `hook` line in `audit.jsonl`, which the app
serves on the Security screen — with the hook's id, event, tool, kind, where it ran (sandbox
backend and whether it isolates), whether it ran, its exit code and time, what it asked for, what
was applied, what was refused, and the SHA-256 of the hooks file. A hook that was refused because no
sandbox exists writes a receipt too. An invalid hooks file with hooks switched on refuses every
tool call with the parse error, rather than running without the hooks the owner believes are there.

### A8. A hook takes the agent down (availability)

A slow or failing hook on every tool call is a denial of service, and a hook that fails open is a
guard that is not there.

**Contained by:** a per-hook `timeout` (default 10 s, ceiling 60 s). A hook that fails — times out,
exits non-zero, prints something that is not a JSON object — follows its `on_error`: `deny` by
default (the call is refused and the receipt says why), or `ignore` when the owner wrote that. The
cost per tool call is measured, not assumed (below).

## What is deliberately not offered

- **No `allow`, no argument rewriting, no session-start command.** Each of those is a way to widen
  or to execute without a tool call to anchor the receipt to.
- **No hooks from a repository.** Claude Code and others read hooks from the project folder; here a
  repository cannot carry a hook at all, because a repository is the most common carrier of the
  injected text this design exists to contain.
- **No route that writes the file**, and no bridge route that reads it.

## Where hooks apply

Every assembly that goes through `govern_step` (`chimera/governance/profile.py`): the desktop Code
screen and the API's runs (`code_api.assemble_registry`), `chimera chat`/`assist`/`tui`
(`right_hand.py`), every surface built by `governed_profile` (among them the ACP server, the cron
jobs, the Kanban lanes and the messaging bots) and a guarded
`chimera solve` started inside a conversation. The app's chat installs no kernel, so its guard
(`api/posture.guard_chat_registry`, on by default through `CHIMERA_GUARD_CHAT`) installs the hooks
itself, asking the same person on the same card. A `chimera solve` without `--guard`, and the app's
chat with its guard switched off, assemble no protection layer at all and run without hooks; that
is stated as a residual in the audit's row 13. Hooks apply whatever the governance mode is — off,
observe or enforce — because the owner switched them on separately; and a hook's `ask` goes to the
owner's approver, never to `observe`'s approve-everything one, which would turn a hook's question
into a yes.

## The file

```json
{
  "hooks": [
    {"id": "no-push", "event": "pre_tool", "tools": ["run_shell"], "pattern": "git\\s+push",
     "decision": "deny", "reason": "pushes go through me"},
    {"id": "lint", "event": "post_tool", "tools": ["write_file", "edit_file"],
     "command": "python lint_hook.py", "timeout": 20, "on_error": "ignore"}
  ]
}
```

| key | meaning |
|---|---|
| `id` | the name the receipts and the model see |
| `event` | `pre_tool` or `post_tool` |
| `tools` | tool names the hook applies to; `["*"]` (the default) is every tool |
| `pattern` | optional regular expression (case-insensitive) over the call as the kernel reads it |
| `decision` + `reason` / `note` | a static hook: `deny`, `ask` or `annotate` (`post_tool`: `deny` or `annotate`) |
| `command` | a shell hook: run as written, in an empty folder holding `event.json` |
| `timeout` | seconds, 1–60, default 10 |
| `on_error` | `deny` (default) or `ignore` |

A key not in this table makes the file invalid, and with hooks on an invalid file refuses every
call: `"allow": true` must be an error the owner sees, not a line that silently does nothing.

A shell hook reads `event.json` — `{"event", "tool", "arguments"}` with document bodies replaced by
their size, plus `{"result": {"chars", "failed"}}` after the call — and answers on stdout with
nothing (no opinion) or one JSON object: `{"decision": "deny" | "ask" | "annotate", "reason": "…",
"note": "…"}`. Exit code 0 is required; anything else is a failure and follows `on_error`.

## The cost, measured

Per tool call, on the reference Windows machine (no OS sandbox), from
`tests/test_hooks_only_tighten.py::test_the_overhead_per_tool_call_is_measured`, which prints the
numbers on every run so they can be re-read anywhere:

| configuration | per call |
|---|---|
| hooks off | no wrapper installed — the registry is the object the kernel built, so zero by construction |
| ten static hooks, none matching | +10.7 µs over a bare call of 0.7 µs |
| one shell hook, sandbox faked (temporary folder + `event.json` only) | +18.8 ms |
| one shell hook that starts a real Python process on the host | ~300 ms |

A shell hook on `pre_tool` for every tool is therefore the expensive configuration: the process
start dominates, and on Windows it is a large fraction of a second per call. Static hooks cost
nothing a person would notice.

## The tests that hold this document

- `tests/test_hooks_only_tighten.py` — a hook cannot widen (every non-tightening answer is ignored
  and listed; the arguments are the model's; an approved `ask` still meets the kernel); replay of
  A1–A8 against the design; receipts; the overhead measurement.
- `tests/test_the_agent_cannot_install_a_hook.py` — the write tools, the file routes, the shell and
  code tools and the bridge's settings route all refuse the hooks file or the switches.
