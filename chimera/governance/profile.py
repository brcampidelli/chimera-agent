"""One place that assembles a governed registry — and the observation mode that comes before it.

Every surface used to build its own stack, and five of them built none at all: `serve`, the cron
dispatch, the MCP server, the A2A endpoint and the messaging adapters all constructed
`default_registry(workspace)` raw. `CHIMERA_TOOL_ALLOWLIST` had three call sites and none of them
were these, which made `config.py`'s claim that the lists "apply on every surface" false for exactly
the surfaces that run unattended.

The fix is not more calls to remember. It is one function, plus a test that fails the build when a
surface is assembled without it — because a convention held by discipline decays at the rate people
join and leave a project, and this one is held by nobody at 3 a.m.

**Observation before enforcement, and that ordering is a safety property rather than caution.** With
`narrow_on_taint` on and no approver, a job that reads a news feed or a ticket becomes unable to
write for the rest of its run — and the refusal arrives as an ordinary observation string, so the
agent reads it, carries on, and the run reports success having done nothing. On a position guardian
that is real money unwatched behind a green tick. So `observe` runs the whole stack, records every
action that WOULD have been refused, and refuses none of them. Turning it to `enforce` is a decision
someone makes with that count in front of them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from chimera.config import Settings

_log = get_logger("governance.profile")

#: What a surface asked for, so the answer can be audited later. Not a bool: "this surface has no
#: governance" and "this surface was assembled in observe mode" are different states and the
#: distinction is what the rollout is about.
MODES = ("off", "observe", "enforce")


def governed_profile(
    registry: Any,
    *,
    settings: Settings,
    home: Path,
    mode: str = "",
    allow: str | None = None,
    deny: str | None = None,
    surface: str = "",
) -> tuple[Any, Any]:
    """Wrap ``registry`` in the deployment's governance. Returns ``(registry, approvals)``.

    The order is the same one `solve` has always used and it is load-bearing: the allowlist filters
    first (a tool that is not there cannot be reasoned about), the kernel wraps next, and the taint
    ledger is outermost so it sees the same calls the kernel does.

    ``approvals`` is the ledger of what was refused or granted. A caller that throws it away is a
    caller that cannot tell "the job did its work" from "the job was not allowed to" — which is the
    failure this whole module exists to make impossible.
    """
    from chimera.governance import ApprovalLedger, TaintLedger, TrustKernel
    from chimera.governance.approval import allow as allow_everything
    from chimera.governance.approval import approver_for
    from chimera.governance.governed_tool import govern_registry
    from chimera.governance.ledger_tool import ledger_registry

    resolved = (mode or settings.governance_mode or "off").strip().lower()
    if resolved not in MODES:
        _log.warning("unknown governance mode %r on %s: treating as 'off'", resolved, surface)
        resolved = "off"

    approvals = ApprovalLedger()
    if resolved == "off":
        return registry, approvals

    from chimera.governance.audit import AuditLog

    audit = AuditLog(home / "audit.jsonl")
    # In observe mode the approver says yes to everything and writes down that it did. Every call
    # that reaches an approver is one the policy WOULD have refused, so `approvals.granted` is
    # exactly the report a rollout needs: what enforcement would have cost, per surface, measured
    # rather than guessed.
    approve = (
        allow_everything(approvals)
        if resolved == "observe"
        else approver_for(settings.approval_mode, approvals)
    )

    from chimera.governance import restrict_registry

    allow_names = (
        [x.strip() for x in allow.split(",") if x.strip()]
        if allow is not None
        else (settings.tool_allowlist or None)
    )
    deny_names = (
        [x.strip() for x in deny.split(",") if x.strip()]
        if deny is not None
        else list(settings.tool_denylist)
    )
    if allow_names is not None or deny_names:
        registry = restrict_registry(registry, allow=allow_names, deny=deny_names, audit=audit)

    registry = govern_registry(registry, TrustKernel(audit=audit), approve=approve)
    registry = ledger_registry(
        registry, TaintLedger(), audit=audit, narrow_on_taint=True, approve=approve
    )
    _log.info("governance %s on %s", resolved, surface or "(unnamed surface)")
    return registry, approvals
