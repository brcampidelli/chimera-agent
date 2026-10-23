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
  sandbox as the agent's shell. A verifier declared in `.chimera/verify.toml` is still a shell
  command: on a non-isolated host it runs only when the project has an active `shell` grant and
  `CHIMERA_HOST_EXEC` does not deny host execution. Without that grant it abstains; inferred
  commands from build files continue through the `CHIMERA_HOST_EXEC` confirmation and abstain if
  declined. A project grant permits the configured command but does not make the host sandbox
  isolated (`CHIMERA_VERIFY_NETWORK=1` gives a docker verifier network; on kernel sandboxes it
  cannot, so a verifier requiring network runs on the host only when otherwise authorized). Treat
  the config and its command as executable project content; review it before granting shell. If a turn
  edits the configured verifier during the turn, verification abstains; the command is captured
  before the turn and requires a durable owner approval bound to its exact SHA-256 hash before first
  execution (and again whenever the command changes). An optional `setup` key in that file names
  a project environment-preparation command. It requires its own exact-hash owner approval and the
  same active project shell grant, runs at most once per workspace and command hash, and failures
  are reported to the agent without aborting the turn. Approvals and the prepared marker are kept
  under Chimera's home, not in the checkout. Grants and approved hashes are inherited only by
  durable worktrees created by Chimera for that registered project; arbitrary Git worktrees do not
  inherit them. The app-worktree registry is stored under ``CHIMERA_HOME``. A process that already has
  shell access on the host can write there, just as it can edit the project registry itself. This approval
  binds only the command string,
  not its effects: `pytest`, `make test`, and `npm test` execute project-controlled files such as
  `conftest.py`, `Makefile` recipes, and package scripts, which the agent may also have edited.
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

### Your own phone or browser: pairing a device

A guest link is for somebody else. For the owner on their own phone there is pairing: `chimera pair`
(or **Pair a device** in Settings › Access) prints a code that works **once, for five minutes**,
with a QR of the pairing address. It is typed into a page served by a second LAN listener — closed at
every start and opened only by the owner (`chimera pair --lan`, or **Open** on the card) — and
becomes a 30-day cookie (`HttpOnly`, `SameSite=Strict`, `Secure` over https) signed with a key
derived from the owner's device secret (`<home>/devices.key`). Five wrong codes void every live
code; five from one address shut that address out for fifteen minutes.

A paired device is the owner **for reading and talking**: conversations, turns, guidance, answers to
the agent's own questions, and refusing an approval. It is not the owner's screen. Approving still
takes each question's code (study 30, S30-30), and the routes that hand out access, change settings,
grant folders, start bots or stop the service answer `403` to a device. The Access card lists every
device with its last use and network (`192.168.1.x`); **Revoke** ends one at once, including a live
stream it has open, and **Revoke all devices** replaces the secret, so no cookie issued before then
verifies. The desktop bridge cannot pair, mint a code, list or revoke.

Away from home, Chimera runs **no relay of its own**. If you want to reach the device listener from
outside, put a tunnel you control in front of it — [Tailscale](https://tailscale.com/) on both
devices, or a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
to `http://127.0.0.1:<port>` — and decide that consciously: it extends the same door to wherever the
tunnel reaches. Never bind the app itself to `0.0.0.0` for this.

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

### Full control through the bridge

The desktop bridge (`chimera mcp desktop`) has three switches, each meaningless without the one
before it: **Allow Claude to operate this app** (what the screens do, under the owner's posture),
**Full control** (answering approvals, editing the settings that are not the owner's), and, since the
owner's decision of 2026-10-18, **Full control through the bridge** on Settings > Security.

- **Only the screen turns it on.** `CHIMERA_BRIDGE_FULL_CONTROL` is not in the Settings screen's
  allowlist, so `PATCH /api/config` refuses it to every caller and the bridge's `settings.edit`
  refuses it by name. Its one route, `POST /api/security/bridge-full-control`, refuses any request
  the bridge forwarded, and turns the switch on only for a request that carries the screen
  credential (above) — the owner's bearer token alone is `service.token`, readable on disk. The
  screen asks for confirmation first. Turning it off needs no screen: it only narrows.
- **What it adds.** The owner's decisions (folder grants, saving an agent with its tool grants,
  remembered approval rules, reject-all, remembering a project, answering a settings suggestion,
  plugins and marketplaces, worktree cleanup), the routes that take the person's `confirm: true`
  (restoring, deleting and exporting a design, the storage card's cleanups, restart and reset), the
  owner's settings — posture, guards, approvals, caps, models and scheduling, written instead of
  suggested — and opening a screen in the running app (`ui.navigate`, an address checked against the
  app's own screens, never a URL).
- **What it never adds.** Credentials: no key, token, password or OAuth secret is read or written,
  the Runner (a command outside governance can read the `.env`) and adding an MCP server or signing
  in to one stay the owner's, and the key vault, the agent's read of Chimera's `.env` and the
  provider base URL are not writable. And who can reach the app: pairing, sharing, the LAN door, the
  server token, the messaging bots and their allowlists. Those refusals are in
  `bridge_routes.FULL_CONTROL_NEVER` and `FULL_CONTROL_NEVER_SETTINGS`, with the sentence a client
  receives.
- **The kernel still decides first.** An approval answers a REVIEW; a kernel BLOCK never reaches an
  approver, so no yes — the screen's or the bridge's — runs it. The per-run body checks stay too: a
  run the bridge starts uses the configured models and posture; with full control those are changed
  in the settings, where the change is recorded.
- **Visible and recorded.** While it is on, every screen of the app shows a banner with a one-click
  **Turn off**, and every call made on its authority writes an audit entry (`via: "bridge"`,
  `scope: "full_control"`, the route id, the status and the ids it named — never a body or a value).
  The Security tab lists the newest of them, and the switch's own changes.
- **Honest limit.** The switch lives in `.env`, like the other two: a process that can write that
  file can turn it on without the screen. The agent's own tools refuse the file, and so does the
  bridge; anything else running as the owner does not.

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
  `|`, `>`, `$(`…) never matches. Only the owner creates or revokes one; the bridge cannot, unless
  the owner turned full control through the bridge on (above).
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


## Session permission bypass limits

The optional code-session permission bypass is authenticated with an HMAC key generated in process memory; that key is never written to disk or inherited by child processes. Persisted `bypass_permissions` flags and queued `via_screen` attestations without a valid HMAC are ignored. The permission choice consequently lasts only until the app/service process restarts, when bypass defaults off. A shell can still edit the JSON, but cannot forge the attestation. The lexical hard stops refuse detected Git pushes, package/container publication and selected GitHub API mutations, recognized secret-file tokens (including `.npmrc`, `.netrc`, `.pypirc`, `.git-credentials` and AWS credentials), and selected HTTP upload/data options or remote `rsync`/`scp` destinations. These are heuristics, not a security boundary: glob-expanded names such as `cat .e*`, dynamically assembled strings such as `python -c "open('.'+'env')"`, alternate encodings, wrappers, or commands outside the recognized syntax cannot be guaranteed to be detected. A malicious page can still bypass them. Use the isolated sandbox for untrusted work.
