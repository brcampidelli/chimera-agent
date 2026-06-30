# Security Policy

Chimera is an autonomous agent that can run shell commands, edit files, call external
APIs and modify its own skills. We take its safety surface seriously.

## Supported versions

Chimera is in early alpha (`0.0.x`). Security fixes are applied to the latest `main`.

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
  secret material) are blocked or escalated. Enable with `--guard`.
- **Static validator** — self-authored skills must pass a static check (the constrained
  edit surface) before they are kept; learned skills are prompt templates, not executable code.
- **Verify-or-revert** — autonomous changes are snapshotted and reverted if verification fails.
- **Human-in-the-loop** — agent-proposed crons are created **disabled**, pending approval.
- **Audit log** — governance decisions and evolution changes are recorded.

Treat secrets as server-only, run untrusted tasks in a sandbox/container, and review the
audit log when granting broad autonomy.
