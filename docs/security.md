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

## Measured, not asserted

```bash
chimera redteam
```

runs an injection corpus through the stack. On the built-in corpus the taint layer cuts the
**attack success rate from 100% to ~14%** — and the report *names* what still gets through
(exfiltration via an allowed tool) rather than claiming 100%.

## Honest limits

This measures whether an *already-injected* agent's harmful action is stopped — not whether
the model can be injected in the first place. Free-form reasoning over untrusted prose, and
exfiltration through legitimately-needed tools, remain open problems (tracked as
[issue #5](https://github.com/brcampidelli/chimera-agent/issues/5)).

The full, always-current policy lives in
[SECURITY.md](https://github.com/brcampidelli/chimera-agent/blob/main/SECURITY.md), including
how to report a vulnerability.
