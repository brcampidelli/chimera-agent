# Security Policy

Chimera is an autonomous agent that can run shell commands, edit files, call external
APIs and modify its own skills. We take its safety surface seriously.

## Supported versions

Chimera is in early development (`0.1.x`). Security fixes are applied to the latest `main`.

## Reporting a vulnerability

**Please do not open public issues for security problems.**

Report privately via GitHub Security Advisories:
<https://github.com/brcampidelli/chimera-agent/security/advisories/new>

Or email the project: **chimeraagent01@gmail.com**.

Please include:
- a description of the issue and its impact,
- steps to reproduce (a minimal command or repo state),
- the Chimera version (`chimera version`) and your environment.

We aim to acknowledge reports within a few days and to coordinate a fix and disclosure.

## Built-in safeguards (and their limits)

Chimera ships defense-in-depth, but **none of these replace running it in an isolated
environment** when you grant autonomy:

> **Measured, not asserted.** `chimera redteam` runs a corpus of injection attacks (a
> malicious page/email trying to steer the agent into a harmful tool call) through the
> governance stack. On the built-in corpus (**n=7, illustrative — not a sample size to
> generalise from**), the taint-adaptive allowlist cuts the **attack success rate from 100%
> to ~14% (6/7 blocked)** (destructive shell, backdoor writes, self-modification, and email
> exfiltration; the remaining leak is exfiltration through an *allowed* tool like `http_get`,
> which it names rather than hides). Read the number precisely: every block here comes from
> the **coarse dangerous-tool narrowing** (`DANGEROUS_WHEN_TAINTED`), not the per-action flow
> matcher — so the rate tracks *which tools the corpus attacks*, and a corpus weighted toward
> allowed tools would score worse. It measures whether an already-injected
> agent's harmful action is *stopped* — not whether the model can be injected at all, which
> is the harder, still-open half of #5.

- **Governance kernel** — every governed tool call is evaluated allow/warn/block/review;
  known-dangerous signatures (`rm -rf /`, disk wipes, fork bombs, `curl | sh`, force pushes,
  secret material) are blocked or escalated. Enable with `--guard`. **These are shell-signature
  matches — a cheap first filter, not the security boundary.** An agent can sidestep them (e.g.
  by doing the same thing from a Python script); the real containment is the sandbox below.
- **Per-session tool allowlist** — grant a session only the tools it needs with
  `--allow-tools`/`--deny-tools` (or `CHIMERA_TOOL_ALLOWLIST`/`CHIMERA_TOOL_DENYLIST`). An
  un-granted tool is **dropped from the registry**, so it never reaches the model's schema —
  the agent cannot invoke, or be talked into invoking, what it was not given. Subagents
  inherit the grant. (h/t u/zoharel on r/AI_Agents — capabilities should be explicitly
  allowed per session.)
- **Capability ledger + taint tracking** (`--taint`) — records a per-run ledger of what was
  fetched, written, read and executed (a replayable JSONL), marks web/external content as
  **tainted**, propagates that taint into files it flows into, and escalates to **review** when
  an action *executes or self-modifies on tainted input* (the "downloaded X, then ran X" flow a
  memoryless lexical rule misses). **Honest limits — what still gets through:** this is heuristic
  reference/verbatim-flow taint, not true dataflow — a model laundering the content (paraphrase,
  re-encode) defeats it; it is sequence-aware *review* and observability, never a hard block, and
  it does **not** solve the data-vs-instructions problem. Concretely, taint is **dropped** across:
  a **sub-agent hand-off** (each worker gets its own ledger; cross-worker collusion is caught by a
  *post-hoc* monitor that warns, it does not prevent), the **fusion engine** and any
  **summarisation/compaction** (a synthesiser's whole job is to paraphrase, which erases a verbatim
  match). Coverage by surface: the `solve`/`crew`/`hierarchy` CLI paths and the **API server** track
  taint (the server arms the narrowing gate by default — see `CHIMERA_TAINT_NARROW` below); the **TUI
  and scheduler still do not**. It also only *escalates to review*, so with no approver present a
  tainted action is refused, not silently run, but nothing is hard-blocked.
  **Untrusted local files:** by default the workspace is *trusted* — `read_file` does not taint,
  because `chimera solve` usually runs on your own repo and tainting every file read would make
  `--taint` fire on everything. If you run against code you do **not** control (a third-party repo, a
  PR branch, anything downloaded), set **`CHIMERA_TRUST_WORKSPACE=0`** so a `read_file` of a poisoned
  source file taints the run like a fetched page does. (A red-team found in 2026-07 that without this,
  a poisoned local file read under `--taint` reached a dangerous tool ungated — the same payload was
  blocked when it arrived via `scrape`. `read_document` and `transcribe_audio` always taint, since a
  document or recording is external by nature.) The sandbox is still the real boundary for hostile
  code — run it under `CHIMERA_SANDBOX=docker`. This layer is defence-in-depth on top of it. (h/t
  u/Dependent_Policy1307, u/Far-Stable2591, u/zoharel on r/AI_Agents.)
- **Quarantined reader (dual-LLM / CaMeL)** — the structural answer to injection: untrusted
  content is read by a *tool-less* model that can only emit schema-validated JSON
  (`QuarantinedReader`, surfaced to the agent through the `extract` tool), and the privileged
  agent acts only on those fields. Even a fully hijacked extractor can only produce *wrong field values* —
  never a new instruction, tool call, or capability, because the output is bounded by the
  Pydantic schema, not by the model's obedience. **Honest limit:** this only covers the
  *structured-extraction* shape ("pull the sender/price/date"); a task that genuinely needs
  reasoning over free-form untrusted prose still exposes the surface — that part of #5 is
  open.
- **Taint-adaptive allowlist** — with `--taint`, once a run consumes untrusted content the
  grant *narrows*: dangerous tools (`run_shell`, `execute_code`, `write_file`, `send_email`)
  require approval for the rest of the run, even when the specific call has no tainted
  reference. This is the coarse net for *laundered* flows — an injection paraphrased past
  the per-action ref/flow matcher — trading some convenience for a smaller blast radius.
- **Data-fencing on fetched content (spotlighting)** — with `--taint`, untrusted tool
  results (web pages, search, email) are returned to the model inside explicit
  `<<external-data>>` markers, and the agent's standing instructions say marked content
  is data, never instructions. **Honest limit:** this is a prompting mitigation with a
  known failure rate — a well-crafted injection can still talk through it. It lowers the
  hit rate; the sandbox and taint escalation remain the containment.
- **Provenance on durable artifacts (anti-poisoning)** — memories and learned skills born
  from a run that consumed untrusted content are marked `tainted`; a tainted-run skill is
  held **pending** (excluded from retrieval) until approved via `chimera skills-approve`,
  and tainted memories are labeled `[unverified]` on recall. This targets the
  self-reinforcing-injection loop ("Zombie Agents"): a poisoned page must not silently
  become a trusted skill or memory that hijacks future runs. **Honest limit:** provenance
  is per-run and coarse — it flags *everything* born from a tainted run, and it cannot
  detect poison that arrives through channels the ledger doesn't see.
- **Static validator** — self-authored skills must pass a static check (the constrained
  edit surface) before they are kept; learned skills are prompt templates, not executable code.
- **Verify-or-revert** — autonomous changes are snapshotted and reverted if verification fails.
- **Human-in-the-loop** — agent-proposed crons are created **disabled**, pending approval.
- **Audit log** — governance decisions and evolution changes are recorded.

Treat secrets as server-only. **The default `local` sandbox is not isolated** — for untrusted
or autonomous work run with `CHIMERA_SANDBOX=docker` (ephemeral, network-off by default),
ideally in a throwaway account or VM rather than your main one.

**Host execution is gated by default, including unattended.** Because most installs have no Docker, a
command the model chooses to run would otherwise execute on your machine. `CHIMERA_HOST_EXEC=ask` (the
default) confirms each host command in an interactive terminal; **headless (no TTY) it refuses**, since
`ask` means a human decides and unattended there is nobody to ask. `allow` runs without asking (the
explicit opt-in an unattended deployment that genuinely needs host execution should set); `deny`
refuses outright. A docker sandbox that silently fell back to local (Docker absent) is treated as host
execution and gated too, so "configured docker" never quietly becomes "ran on the host".

> Changed 2026-07-20 (was: headless proceeded after a one-time warning). That made the shipped default
> effectively `allow` on every server/cron/CI surface — the place host execution matters most. If an
> unattended deployment stopped running shell commands after this change, that is the fix working: set
> `CHIMERA_SANDBOX=docker` to run them isolated, or `CHIMERA_HOST_EXEC=allow` to accept host execution.

**Taint narrowing is armed on the API server.** Once a run consumes untrusted content, the tools in
`DANGEROUS_WHEN_TAINTED` — execution, file writes, **and every outbound channel** (`send_email`,
`send_message`, `send_sms`, `http_post`, `post_webhook`, `create_issue`, `browser`) — require approval.
The server has no tool-level approver yet, so this resolves to a refusal with an explanatory result:
fail closed. Set `CHIMERA_TAINT_NARROW=0` on a deployment that must keep acting autonomously after
reading the web, accepting that a laundered injection could steer those tools. Routing the approval to
the desktop's human-in-the-loop UI (so it can be *answered*, not only refused) is the follow-up.

A plain container isn't a full VM: a container escape typically rides a host-kernel bug, so
hostile input still has a path to local privilege escalation. To harden that boundary without
paying for a full VM, set **`CHIMERA_SANDBOX_RUNTIME=runsc`** to run the docker sandbox under
[gVisor](https://gvisor.dev/) — a userspace kernel that interposes between the container and the
host, intercepting syscalls and shrinking the host-kernel attack surface. It's a drop-in OCI
runtime (requires gVisor installed on the host) and the recommended hardened setup. The endgame
above it is still a real microVM (Firecracker/QEMU/Kata) for genuinely adversarial workloads.
(Thanks to u/zoharel on r/AI_Agents for pressing on the container-vs-VM gap and flagging gVisor
as the pragmatic middle step.)

Review the audit log when granting broad autonomy.
