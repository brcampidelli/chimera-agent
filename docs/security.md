# Security & safeguards

Chimera can run shell commands, edit files, call APIs, and modify its own skills. It ships
**defense-in-depth**, and — this matters — the docs state where each layer *stops*.

!!! warning "The one rule"
    None of these safeguards replace **running it in an isolated environment** when you
    grant autonomy. The default `local` runner is not isolated; use
    `CHIMERA_SANDBOX=docker` (network-off, optionally under gVisor) for untrusted work.

## The layers

- **Governance kernel** — every governed tool call is allow / warn / review / block. A
  cheap first filter of dangerous shell signatures, not the boundary.
- **Sandbox** — an ephemeral, network-off container (`CHIMERA_SANDBOX=docker`), hardenable
  with gVisor (`CHIMERA_SANDBOX_RUNTIME=runsc`).
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

## Measured, not asserted

```bash
chimera redteam
```

runs an injection corpus through the stack. On the built-in corpus the taint layer cuts the
**attack success rate from 100% to ~14%** — and the report *names* what still gets through
(exfiltration via an allowed tool) rather than claiming 100%.

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

## Honest limits

This measures whether an *already-injected* agent's harmful action is stopped — not whether
the model can be injected in the first place. Free-form reasoning over untrusted prose, and
exfiltration through legitimately-needed tools, remain open problems (tracked as
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

The full, always-current policy lives in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), including
how to report a vulnerability.
