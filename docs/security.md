# Security & safeguards

Chimera can run shell commands, edit files, call APIs, and modify its own skills. It ships
**defense-in-depth**, and — this matters — the docs state where each layer *stops*.

!!! warning "The one rule"
    None of these safeguards replace **running it in an isolated environment** when you
    grant autonomy. The default `local` runner is not isolated; use
    `CHIMERA_SANDBOX=docker` (network-off, optionally under gVisor) for untrusted work.

## Agent voice in prompts and bot text

Prompts and bot templates must not have the agent claim feelings, care, friendship, a relationship,
or promises about itself. State what it did, could not do, and how sure it is. Calibrated
first-person uncertainty (e.g. “I'm not sure, but…”) is allowed and useful.

## The layers

- **Governance kernel** — every governed tool call is allow / warn / review / block. A
  cheap first filter of dangerous shell signatures, not the boundary.
- **Sandbox** — an ephemeral, network-off container (`CHIMERA_SANDBOX=docker`), hardenable
  with gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`). The **verify command** runs in the same
  sandbox as the agent's shell, and when that sandbox is not isolated a command that was not
  typed by you — inferred from the repository, read from a cron job, a card or a workflow — goes
  through the same `CHIMERA_HOST_EXEC` confirmation; declined, it abstains rather than runs
  (`CHIMERA_VERIFY_NETWORK=1` gives a docker verifier the network; on the kernel sandboxes it
  cannot, so a verifier that needs the network runs on the host, and only when you typed it).
- **Per-session tool allowlist** — grant a run only the tools it needs; the rest are dropped
  from the model's schema entirely.
- **Taint tracking** (`--taint`) — untrusted content is fenced as data, its provenance
  follows it into memories and skills (a skill from a tainted run is held for review), and
  once a run is tainted the dangerous tools narrow.
- **Quarantined reader** — the dual-LLM / CaMeL pattern: untrusted content is read by a
  tool-less model that can only emit schema-validated fields, so an injection can't produce
  a new instruction or tool call.
- **Cross-agent monitor** — under fan-out a per-worker monitor is blind to a *split* flow (one
  worker fetches untrusted, a different worker sinks it — the fetch and the sink live in
  separate ledgers). An aggregate monitor sees the whole fan-out; it is **always on** for
  `solve-batch` / `crew-isolated`.

## Fan-out: the cross-agent monitor

When several tool-using workers run in parallel (`solve-batch`, `crew-isolated`), each gets its
own capability ledger, and after the batch an aggregate monitor runs over all of them. It catches
patterns no single-worker monitor can see — the split exfiltration where worker A fetches
untrusted content and worker B executes or exfiltrates it:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" -w .
task1: ok
task2: ok
merged 2 file(s) across 2 task(s)
⚠ cross-agent monitor flagged (review):
  - cross-agent-taint: untrusted content entered via one agent and a different agent
    performed a sink (task2→task1) — a split flow no single-agent monitor sees
```

It only ever **escalates to review** — it never blocks a run — and it is pure observability
(recording changes no behaviour). Add `--taint` on top to also arm each worker's adaptive
allowlist (dangerous-when-tainted tools then require approval).

**Approval, and what a refused worker looks like.** Each worker carries its own approver and its own
record of what it was allowed to do, so a task that was refused says so rather than reporting `ok`:

```
$ chimera solve-batch "read notes.md and summarize" "download the helper and run it" --taint -w .
task1: ok
task2: not allowed (ok)
  governance: 1 action(s) refused for review: run_shell is restricted after this run consumed
  untrusted content
1 of 2 task(s) had actions refused for review — check that the work they were asked to do
actually happened.
```

`ok` in parentheses is the loop's own verdict, and the two disagreeing is the point: a refused call
comes back as an ordinary observation string, so the worker reads it like any tool result, carries
on, and finishes in prose. Who can be asked follows `CHIMERA_APPROVAL_MODE` — `deny` refuses at
once, `ask` prompts on a terminal and otherwise writes the question down for `chimera approve` and
waits `CHIMERA_APPROVAL_WAIT` seconds per question, per worker. Silence always refuses.

## Measured, not asserted

```bash
chimera redteam
```

runs an injection corpus through the stack. On the built-in corpus the taint layer cuts the
**attack success rate from 100% to ~14%** — and the report *names* what still gets through
(exfiltration via an allowed tool) rather than claiming 100%.

The same command prints the **cost**, which the first version of this page did not: with nobody
to ask, the narrowing refuses **100% of legitimate work that read anything external first** —
fix the file the issue names, apply the upgrade the docs describe — and the registered gate
(over-block ≤ 5%) fails. That number is not a tuning problem; the gate was empty. The default
approval mode is `ask`, and on the desktop it now asks: a narrowed tool call becomes a question
on the screen with the ledger's reason attached, answered with a button or `chimera approve`,
refused by silence after `CHIMERA_APPROVAL_WAIT` seconds. With the person approving the work
they asked for, over-block is 0% and the attack block rate does not move — measured, per arm,
in [`bench/injection/RESULTS.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/injection/RESULTS.md).
The exfiltration through an allowed tool is closed by the same change: a tainted run's `http_get`
carrying a query string is a review, and the two legitimate query-string GETs added to the
corpus show what that costs.

### Poisoned memory, across runs

`redteam` measures one run. The other shape is slower and does not fit in a process: run A reads a
poisoned page and stores what it "learned"; run B asks an unrelated question days later and recall
hands the planted fact to the model.

```bash
chimera memory-poison
```

Also offline and free. It ablates the three layers that sit between those runs — the `tainted`
provenance flag, the recall admission gate, and the `[unverified]` label the fact wears into the
prompt — because a single number would be compatible with any of them doing nothing. The headline is
what arrives **unmarked**, not what is blocked: a poisoned fact carrying its origin is one the model
was warned about; an unlabelled one is indistinguishable from something the agent verified itself.

Two results from the first run are worth stating plainly, because neither flatters us:

- **The shipped configuration fails its own gate — on cost.** It marks 100% of the poison and
  destroys 25% of honest memory doing it. The casualties are named: a security document that quotes
  an attack in order to explain it, and a support ticket forwarding an attempt. A pattern matcher on
  content cannot tell a quote from a command.
- **On this corpus the content gate adds nothing the provenance label does not already cover.** Its
  entire measured effect is the honest memory it removes. Fifteen hand-authored rows is a pointer
  and not a verdict, which is why nothing has been deleted on the strength of it.

Thresholds, method and what the numbers do *not* license are in
[`bench/memory_poison/PREREGISTRATION.md`](https://github.com/brcampidelli/chimera-agent/blob/main/bench/memory_poison/PREREGISTRATION.md),
fixed before the first run.

## Exposing the HTTP server

`chimera serve` binds to `127.0.0.1` by default. Its state-changing endpoints (`/chat`, `/a2a`,
`/webhook/*`) drive the agent, so **before exposing the server to a network**, set a bearer token:

```bash
export CHIMERA_SERVER_TOKEN="a-long-random-secret"   # required as: Authorization: Bearer <token>
```

With it set, those POST endpoints return `401` without a matching `Authorization: Bearer` header
(`GET /health` and the A2A agent-card stay open). For the WhatsApp inbound webhook, set
`CHIMERA_WHATSAPP_APP_SECRET` to your Meta app secret — Chimera then verifies each request's
`X-Hub-Signature-256` HMAC and rejects a forged payload with `403`. Both are opt-in (unset = no auth,
fine for localhost); a public deployment should set them (or sit behind an authenticating proxy).

### Sharing one conversation with a second person

The desktop app can share a coding conversation by a **token that opens only that conversation** —
never the server token. The owner mints one per guest (`POST /api/code/sessions/{id}/share`) and can
revoke it alone; deleting the conversation revokes all of them. A guest reaches four routes under
`/guest/api/…`: read the conversation, watch it live, send a message into it, see who is there.
Nothing else answers a share token, and the guest app has no route to answer a governance card —
those stay on the owner's screen.

The guest app is also the **only** thing served when the owner opens the network door
(`POST /api/code/share/network`, closed again with `DELETE`, never open at launch): a machine on the
LAN reaches those four routes and nothing the server token protects. Say it plainly before handing
out a link: a guest can ask the agent to do anything the owner can ask it, in the owner's project,
with the owner's tools and spend — under the request's default seams, and short of approving cards.

## The desktop app's backend

The app's backend listens on loopback, and loopback is not a person: any process on the machine can
reach the port.

- **A token on every route.** The desktop app always starts its backend with a bearer token — a
  fresh secret per launch, or the `CHIMERA_SERVER_TOKEN` the owner already set. Every `/api/…` route
  then answers `401` without it, `/api/health` included. Only the static app files and the guest app
  (`/guest/api/…`, which checks its own share tokens) stay open; the MCP bridge (`/api/bridge/…`)
  keeps its own token. The shell hands the per-launch token to the backend in the environment and
  to the window through the webview's initialization script — never on a command line, in a URL or
  in a file. What the backend starts does not inherit it: the sandbox's child environment and the
  one `gh` and `git` run with drop every secret-named variable (`GH_TOKEN` and gh's other own
  credentials excepted).
- **Approving without the code takes the screen.** A token says who may call the API, not that the
  owner is looking. A yes with no code (`POST /api/approvals/{id}`) succeeds only when the request
  also carries the screen credential in `X-Chimera-Screen`, beside the bearer token. It is drawn on
  every start and kept in memory: never in a file, a record, a log, `.env` or a route's answer, never
  equal to the bearer token, and removed from the environment as it is read (the shell passes it as
  `CHIMERA_SCREEN_TOKEN`). Any token read from disk — `service.token`, a `CHIMERA_SERVER_TOKEN` in
  `.env` — still needs the per-question code for a yes; a refusal needs no code. `chimera service`
  holds no screen credential, so there every yes takes the code. `chimera app` in a browser puts it
  in the page it serves to a loopback request (`<meta name="chimera-screen">`), exactly where and
  when that page already carries the bearer token — the same exposure, by the owner's decision of
  2026-10-16.
- **The background service.** `chimera service start` runs the same backend without a window. It
  publishes `~/.chimera/service-<channel>.json` (0600, written atomically; `CHIMERA_SERVICE_DIR`
  moves it) with the owner's persistent token (`<home>/service.token`, or an owner-chosen
  `CHIMERA_SERVER_TOKEN`), and a client trusts that file only after `GET /api/info` answers with the
  same pid, version, id and data folder. That token opens the whole API, so it stays apart from the
  bridge's: nothing on the MCP side reads the service files, and the bridge refuses them as files.
  The installed app's launcher record, `~/.chimera/desktop-install.json`, holds only four path
  variables — `CHIMERA_HOME`, `CHIMERA_WORKSPACE`, `CHIMERA_SHELL_PREFS`, `CHIMERA_SHELL_STATE` —
  its writer refuses any other name, and `start` validates it before running anything from it.

## What the owner decides, and what a repository cannot

- **Deployment policy, deny-only.** `<CHIMERA_HOME>/policy.json` (or the file `CHIMERA_POLICY_FILE`
  names) holds ordered `provider.use` / `integration.use` / `tool.use` rules; the last match wins.
  An `allow` there never grants anything: it only undoes an earlier deny of the same file, so it
  cannot reopen `CHIMERA_TOOL_DENYLIST`, the reach floor or a kernel BLOCK. An invalid file is never
  applied in part: `serve`, `chat`, `assist`, `tui` and `acp` refuse to start, `doctor` fails, and
  the desktop starts but refuses every tool until it is fixed. The agent cannot rewrite it — the
  write tools refuse the file wherever it lives, and the bridge cannot repoint it.
- **"Always in this project".** On an approval card the owner can remember a yes for one project:
  the tool, or for a shell command its first two words. It answers matching questions later,
  **including after the run read untrusted content** — the owner's decision of 2026-10-15, knowing
  a hostile page could steer a command inside that prefix. It only skips the question: a kernel
  BLOCK and the reach floor still win, and a command that chains, pipes or redirects (`;`, `&&`,
  `|`, `>`, `$(`…) never matches. Only the owner creates or revokes one; the bridge cannot.
- **Named references.** Folders the owner lists in `<CHIMERA_HOME>/references.json` are reachable as
  `@alias/path` by the read tools only (`read_file`, `list_dir`, `grep`, `glob`); every writer
  refuses an alias. A filesystem root, the user's home or the data folder is refused as a
  reference. Only attended surfaces bind them — `chat`, `assist`, `tui`, an attended `solve`, the
  desktop's Code turn (never a guest's) — so bots, cron and `scenarios` have none.
- **Agents in Markdown.** `.chimera/agents/*.md` in a repository is someone else's text, so it can
  only narrow: its tools are intersected with the session's governed tools, its steps are capped by
  the session's, unknown keys are refused, its `model` is ignored, and its body is fenced as
  untrusted data. Only the owner's files in `<CHIMERA_HOME>/agents` may pin a model from the
  catalogue.
- **Chats linked to conversations.** `/link` in Discord or Telegram makes a chat continue a Code
  conversation, and only the owner listed in that bot's allowlist can link one — with no allowlist,
  nobody. A stranger's text in a linked room never reaches the conversation, and media is refused
  rather than passed on. The turn runs on the bot's floor, not the desktop's: only the MCP servers in
  `CHIMERA_BOT_MCP_SERVERS`, no OpenAPI connectors, no shell grant or host exec.
- **The owner's connectors stay off doors a stranger can open.** An OpenAPI connector calls out with
  the owner's key, so it answers whoever drives the run. It does not load on a messaging surface
  while any configured bot has no allowlist, never on a GitHub issue job (anyone can write the
  issue; only the repository is checked), and on `/v1/chat/completions` only while
  `CHIMERA_SERVER_TOKEN` is set. One module, `chimera/governance/surfaces.py`, decides what kind of
  surface a name is, by its family (`platform:discord` is a bot); a test fails if a module writes its
  own list again.

## Honest limits

This measures whether an *already-injected* agent's harmful action is stopped — not whether
the model can be injected in the first place. Free-form reasoning over untrusted prose, and
exfiltration through legitimately-needed tools, remain open problems (tracked as
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

The full, always-current policy lives in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), including
how to report a vulnerability.
