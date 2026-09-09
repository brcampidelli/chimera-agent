# The terminal right-hand — what it is, what it does, and what to fix first

**Written 2026-09-08 against `main` at `d90bed8` (0.52.0).** Five read-only studies ran in
parallel — architecture, live use, parity, governance, and the ruler — and every claim below that
carries a `file:line` was re-read in the repository by the reviewer before it was written here.
The live study spent **US$ 0.10** on `openrouter/deepseek/deepseek-chat-v3.1`. Nothing was changed.

"The terminal right-hand" is the project's own name for three commands that share one core:
`chimera chat` (`chimera/cli/main.py:1190-1329`), `chimera assist` (`:1332-1491`) and `chimera tui`
(`:1494-1553`, `chimera/tui/`), all over `ChatSession` (`chimera/interface/session.py`).

## 1 · The verdict, in one paragraph

Since July the terminal has received only cross-cutting patches that came out of desktop audits.
Its one product change — the `chat` command remembering its thread (`37d8d3e`, 2026-08-09) — reached
neither `assist`, nor the TUI, nor the docs, nor the CHANGELOG. Meanwhile the loop and the desktop
gained approvals, receipts, cost ceilings, failure classes, a taint ledger with narrowing, and the
verify command moved into the sandbox — **none of it reaches the terminal**, by a written exemption
whose reason is "attended" (`tests/test_governed_surfaces.py:145-147`). The only gate between the
model and the machine is the host-exec `y/N`, and when that gate says no, **the model invents the
result of the command that did not run**. The one thing in the repository that claims to measure
the right-hand does not touch it. And the docs describing all three surfaces stopped in June.

## 2 · What was found, by angle

### 2.1 The ruler: `chimera scenarios` measures the key, not the right-hand

- It bypasses `ChatSession` entirely: `SingleModelSolver` sends one `Message(role="user")` to the
  gateway at T=0 — no system prompt, no tools, no memory, no transcript (`cli/main.py:6618-6623`;
  `chimera/eval/continuous.py:205-211`).
- All seven checks are substring inclusion (`chimera/eval/scenarios.py:48-53`). **Four of seven
  pass on the echo of their own prompt** (`sentiment`, `extract_emails`, `action_items`,
  `summarize`); `tip` accepts `$120` for 15% of $80, `minutes` accepts `1500`. The unit test proves
  the shape: one canned string, `_MATCHES_EVERYTHING`, is the right answer to all seven questions at
  once (`tests/test_scenarios.py:16-24`).
- At the ceiling since July: 7/7 in three live samples today, `pass^3 = 1.0`, zero flips. A ruler at
  the ceiling carries no information. "Daily" is a word in a docstring — nothing schedules it,
  results go to stdout only, cost is discarded (`run_scenarios` calls `solve()`, not
  `solve_with_cost`), and the whole history is one CHANGELOG line from 0.3.0 with no model and no
  date. `evolve tune` scores the same seven with a *different* solver — two rulers, one name — and
  because the suite is at ceiling its non-regression criterion can only tie.

### 2.2 Governance: no ledger, no kernel, no approver — by exemption

All three build `_apply_tool_allowlist(default_registry(Path(workspace)))` (`main.py:1238-1240`,
`1375-1377`, `1539-1541`): no `TaintLedger`, no kernel, no write region, no owner posture, no
`instructions`. Consequences, each verified:

| | desktop / API path | terminal |
|---|---|---|
| taint ledger + narrowing after an external read | always (`code_api.py:577-663`) | **none** — `record_fetch` never fires; `CHIMERA_TRUST_WORKSPACE`, `CHIMERA_TAINT_AUTHORITY` inert here |
| kernel BLOCK signatures (`rm -rf /`, `mkfs`) | `govern_step` | **none** — with `CHIMERA_HOST_EXEC=allow` nothing refuses |
| `ask` approvals becoming a question | `pending.py` → ApprovalCard | **unreachable** — no approver constructed; `chimera approve` never has a question from chat |
| fenced untrusted tool output | `LedgeredTool` (`ledger_tool.py:44-45, 173-176`) | **raw** — and the system prompt promises the `<<external-data>>` fence (`core/agent.py:82-86`) that only `LedgeredTool` writes |
| owner identity (`agent.json`) in the prompt | passed (`main.py:1925`) | **not loaded** (`main.py:1244`) |
| `CHIMERA_REACH=read_only` | `resolve_posture` | ignored |

The comment inside the `chat` body says the kernel and ledger "are staged behind
`CHIMERA_GOVERNANCE`" (`main.py:1232-1235`); that comment is the only mention of governance in the
command. The persisted transcript is a channel: plain JSON in `<home>/sessions`, no provenance, the
newest thread resumed by default (`main.py:1184-1186`), and the last six turns replayed verbatim
(`session.py:220-223`) — content that was untrusted on day one re-enters on day five unmarked.

### 2.3 Parity: 43 capabilities mapped; the terminal has 14

The full table is in the parity study; the rows that matter daily: no approval, no ledger, no
snapshot/undo or verify after an edit, no `--max-usd`, no cost shown in `chat`, no `usage.jsonl`
(the app's Cost screen is blind to the terminal), no receipt or trace of any kind (every census the
project runs on itself excludes its daily surface), no MCP even with autoload, memory without
project scope, identity not applied, `todo_write` costing prompt for a list nothing draws, no
cancel. The reverse list is short and real: the terminal is the only surface where "ask me before
each command" is literally true; `chat --cascade` works while the Code tab ignores the toggle.

Two docstrings are false. `chat` promises "the same store the desktop app reads" (`main.py:1207-1209`)
— the app deleted its chat screens on 2026-08-07 (`ea27efc`); chat persistence landed on 2026-08-09
(`37d8d3e`); today the desktop uses `<home>/code_sessions` and the terminal `<home>/sessions`.

### 2.4 The code: eight defects, two reproduced by execution

1. **A reply containing `[/]` crashes the REPL after the turn is paid.** `chat` and `assist` print
   the model's text as Rich markup without `escape()` (`main.py:1319`, `1481`; also errors and
   nudges). Reproduced: `MarkupError: closing tag '[/]' has nothing to close`. The TUI fixed this in
   July (`tui/app.py:148`); the REPLs never got it. And `chat` **persists before it prints**
   (`main.py:1318-1319`), with no `try` — an unwritable home or `-s ../escape` raises after the
   answer was paid, and the answer is lost.
2. **The documented fallback is broken.** `tui` calls `chat(...)` as a Python function
   (`main.py:1518`); the arguments it omits receive `typer.OptionInfo` objects — `bool()` is `True`,
   so cascade and `--new` are forced, and `str()` becomes the session name. Reproduced live:
   `TypeError: Object of type OptionInfo is not JSON serializable` on the first turn.
3. `assist --model` and `/model` are no-ops under cascade: `CascadeBackend._route` always uses
   `config.mid`/`config.weak` (`chimera/fusion/cascade.py:146, 169, 189`). Live: all eight routes
   went to `deepseek-v4-flash` with `--model deepseek-chat-v3.1`.
4. `--fuse` never fuses in a REPL with tools: `RoutedBackend.complete` short-circuits on `if tools:`
   (`chimera/fusion/router.py:174`; `cascade.py:144`), and the agent always sends the tool schema.
   Live: three eligible turns, zero fusions — while the TUI labels every turn "fusion — synthesizing"
   and turns streaming off (`tui/app.py`), because the label follows the flag, not the route.
5. "Remember that…" is answered "Got it, I'll remember" and **writes nothing**, with or without
   `CHIMERA_CHAT_MEMORY`: `send()` never calls `_maybe_remember` (`session.py:102-106` vs `:121`),
   and no terminal surface passes `remember_from_chat` (`serve` and the app do: `main.py:1655, 1936`).
6. An unknown slash command goes to the model as a message (`/help` in `chat`: 31 s and a
   capabilities essay; `/foo`: a reply about "/foo"), and `startswith` without a boundary makes
   `/taskforce` run a fusion with "force" (`main.py:1300, 1426, 1448, 1465`).
7. Ctrl-C has three semantics and none is tested: mid-turn in `chat`/`assist` the process dies
   silently (exit 130, nothing saved); in the TUI the focused `Input` binds Ctrl-C to *copy*, and
   `/exit` during a turn leaves the process holding the terminal until the HTTP call returns.
8. Only `chat` persists; `assist` and the TUI forget everything on exit; resume prints "N turn(s)"
   and shows none of them; the 50-turn cap (`session.py:164-166`) erases the beginning of a thread
   **from disk** at every persist after the 51st turn.

### 2.5 Live: what a person sees

Ranked by how much it misleads:

1. **The model fabricates a refused command's output.** With the default `CHIMERA_HOST_EXEC=ask`
   and no TTY, `run_shell` returns `"error: host execution declined (CHIMERA_HOST_EXEC). Not run."`
   as an ordinary observation (`tools/shell.py:87`) — and the answer was *"The command printed
   exactly: marker-42 / 2024"*. Nothing marks the reply.
2. **A read-only workspace is bypassed through the shell and reported as success**: `write_file`
   failed with `Permission denied`, the model wrote `/tmp/note.txt` instead and said *"I
   successfully created the file"*.
3. Three empty replies in ~30 turns, each after the model used the `echo` tool (registered by
   default, `tools/builtin.py:37`), each charged.
4. Noise on every start: the bubblewrap WARNING; on a bad model, three red litellm banners plus a
   `-> fallback_model` warning **with no fallback happening**, and the OpenRouter `user_id` leaked in
   the error text; `INFO fusion tokens…` printed inside the REPL; a "save as a skill?" nudge after
   two "Say only: hi.".
5. `chat` shows no cost, writes no receipt, no usage. The TUI shows cost per turn — and it is the
   surface that persists nothing.

Docs vs reality, `docs/usage.md` §chat/§tui (untouched since 2026-06-30 / 07-12): `/reset` described
with the old semantics; `--fuse` "fuses deep-reasoning turns" **false**; "same flags as chat"
**false**; the fallback claim **false**; `assist` absent from usage.md and the README entirely;
`--session`, `--new`, `--cascade`, `chimera sessions`, `/new`, `/quit`, `/q` undocumented.

## 3 · The plan — ranked, sequenced, sized

Ordered by what a person using this daily would feel first, then by safety. Every step is
no-spend unless marked. Every guard is sabotage-verified before it ships.

### Step 1 — the REPL stops crashing and stops lying (S, one PR)
`escape()` on everything the model wrote (`main.py:1315-1327`, `1477-1489`, `5560-5562`); print
**then** persist, inside `try`; validate `-s` before the first turn; fix the `tui` fallback
(explicit arguments, or a shared factory both commands call); unknown slash → "unknown command",
`/model` etc. matched on a word boundary; `/help` in all three surfaces; pass
`remember_from_chat=settings.remember_from_chat` so "remember that" does what it says or the reply
says it cannot; catch `KeyboardInterrupt` around the turn (persist, say bye); silence the
bubblewrap line after the first start or move it to `doctor`; drop the OpenRouter `user_id` from
error text. Tests: one `CliRunner` script per surface that drives the real loop — today the
`chat` loop has **zero** invocations in the test suite.

### Step 2 — a refusal is visible, and money is visible (S/M)
When any tool returns a decline (`host execution declined`, a taint refusal, a permission error),
the surface prints it under the reply — the TUI's `✗ run_shell` with the reason, the REPL a line —
and the turn's `TurnReport` carries `declined: [...]` so nothing downstream can call that turn a
success. Print the TUI's cost line in `chat`/`assist`; write `usage.jsonl` from every terminal turn
(the app's Cost screen then sees the terminal); write a per-turn trace so the terminal enters the
project's own census. Registered check: the live script from 2.5 item 1 must now show the decline
next to the fabricated sentence.

### Step 3 — the same governance the API has (L)
Build the terminal registry through the governed path: taint ledger with `set_instruction(message)`
per turn, kernel, write region, owner posture, `instructions` from `agent.json` — and an approver
that **prompts** in a TTY (the terminal is the one surface with a guaranteed human; `approval.py:113-137`
already knows how). Remove the three "attended" exemptions from `tests/test_governed_surfaces.py`.
Make the comment at `main.py:1232-1235` true or delete it. Measure before and after: run the
`bench/injection` corpus through `chat` with stub tools (offline, US$ 0) — today it would show
block rate 0/7 on this surface; register the prediction that it matches the API path afterwards.
The TUI needs its own answer to the host-exec prompt (a modal, not stdin) — confirm first with the
live pty script whether the 120 s hang the governance study predicted is real.

### Step 4 — one store, one memory, three surfaces (M)
`assist` and the TUI persist like `chat`; the persisted turn carries provenance (which tool outputs
fed it, whether the run was tainted) so a resumed thread is not a laundering channel; resume shows
the last turns; the 50-turn cap trims the prompt, not the file; do not auto-resume the newest
thread across surfaces without saying which. Fix the two false docstrings. Memory recall scoped to
the workspace like the Code tab (`code_api.py:1137-1139`).

### Step 5 — a ruler that measures the right-hand (M; cents per run)
Rebuild `chimera scenarios` as turn scripts routed through `ChatSession` exactly as `chat` builds
it, with functional checks (a tool reads a file of N lines the harness generates — N is not in the
prompt, so it cannot be echoed; memory across sessions checked by `memory_facts_used` and the
store, not by substring; format checked by equality after normalisation; refusal measured on
**both** sides), k=3 via `chimera.eval.replicated`, results appended as one JSON line per run so
"daily" becomes a series that can show provider drift. Either this, or delete `scenarios` — a
ruler at the ceiling that nobody runs is worse than none, and `evolve tune` currently trusts it.

### Step 6 — parity items, each its own small PR (S each)
`--max-usd` on the three surfaces; `/solve` from a conversation (hand the thread to the verified
loop — the two buttons `code_api.py:1215-1221` describes); MCP autoload in the terminal; `todo_write`
either drawn or removed from the terminal prompt; `--fuse` honest (route after the tool loop, or
label "fusion off while tools are in play"); `assist --model` honoured or refused with a message;
`docs/usage.md` §chat/§assist/§tui rewritten from the code, with a guard like the one that keeps
`commands.md` honest.

## 4 · What this study cannot show

- The TUI host-exec hang (120 s then refusal) is **inferred** from Textual's driver owning stdin;
  the live study ran the TUI headless and with `allow`, so it did not exercise `ask` inside the TUI.
  Step 3 starts by confirming it.
- The three empty replies after `echo` were observed, not explained.
- Everything ran on one model, one machine, one day; the live counts are samples.
- `--fuse` with the real frontier panel was not run (budget); the routing finding is by code and
  by a cheap panel, and holds regardless of panel.

## 5 · Method notes

Three briefs from the reviewer were corrected by the agents and the files won: German does address
the reader in second person on some pages (not on the security page); percentages use a plain space
in `de`/`fr`; and the "cp1252" label for four long-standing Windows test failures was wrong twice —
it is cp850, and it is not the space in the path. A study that only confirms its brief has not
studied anything.
