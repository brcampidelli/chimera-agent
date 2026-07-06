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
  memoryless lexical rule misses). **Honest limits:** this is heuristic reference/verbatim-flow
  taint, not true dataflow — a model laundering the content (paraphrase, re-encode) defeats it;
  it is sequence-aware *review* and observability, never a hard block, and it does **not** solve
  the data-vs-instructions problem. The sandbox is still the containment boundary. (h/t
  u/Dependent_Policy1307, u/Far-Stable2591, u/zoharel on r/AI_Agents.)
- **Quarantined reader (dual-LLM / CaMeL)** — the structural answer to injection: untrusted
  content is read by a *tool-less* model that can only emit schema-validated JSON
  (`QuarantinedReader` / the `quarantine_extract` tool), and the privileged agent acts only
  on those fields. Even a fully hijacked extractor can only produce *wrong field values* —
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
