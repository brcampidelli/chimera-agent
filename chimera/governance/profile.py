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

**But an explicit fence is not part of that rollout, and filing it under the same switch was a
mistake.** `CHIMERA_TOOL_ALLOWLIST` / `CHIMERA_TOOL_DENYLIST` are an instruction, not an inference:
the named tool is in the registry or it is not, there is no legitimate work they can refuse by
accident, and there is nothing to price before turning them on. Gating them behind `governance_mode`
meant the only way to get a fence you had written by hand was to accept a rollout you had not asked
for — so on a stock deployment they reached neither Discord bot, while `config.py` said they applied
"on every surface". They now apply whatever the mode; the kernel and the taint ledger still do not.
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

    from chimera.governance import restrict_registry
    from chimera.governance.audit import AuditLog

    audit = AuditLog(home / "audit.jsonl")

    # --- the explicit fence: applied whatever the mode -------------------------------------------
    #
    # This sits ABOVE the `off` return, and the distinction it draws is the whole point. An owner who
    # writes `CHIMERA_TOOL_DENYLIST=run_shell` has stated an instruction with no ambiguity in it:
    # remove that tool. There is nothing to stage, nothing to measure, no legitimate work it might
    # refuse by mistake — the tool is either in the registry or it is not.
    #
    # The machinery below is different in kind. The kernel and the taint ledger *infer* whether an
    # action is dangerous, and inference can be wrong in the expensive direction: a job that reads a
    # feed and then cannot write for the rest of its run, reporting success having done nothing.
    # That is what `observe` exists to price before anyone turns it on.
    #
    # Filing the two under one switch meant the ONLY way to get a fence you had written by hand was
    # to also accept a rollout you had not asked for — so `config.py` promised the lists "apply on
    # every surface" and, on a stock deployment, they reached neither Discord bot. The desktop app's
    # own chat had already gone the other way and applied them unconditionally, which is how a
    # product ends up with two implementations of one rule that disagree about the default.
    #
    # Nothing changes for a deployment that set neither list: both resolve to empty, the call below
    # is skipped, and `registry` is returned exactly as it arrived.
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
        _log.info("tool fence applied on %s", surface or "(unnamed surface)")

    if resolved == "off":
        return registry, approvals

    # --- the inferential machinery: still staged behind observe/enforce ---------------------------
    #
    # In observe mode the approver says yes to everything and writes down that it did. Every call
    # that reaches an approver is one the policy WOULD have refused, so `approvals.granted` is
    # exactly the report a rollout needs: what enforcement would have cost, per surface, measured
    # rather than guessed.
    approve = (
        allow_everything(approvals)
        if resolved == "observe"
        else approver_for(settings.approval_mode, approvals)
    )

    registry = govern_registry(registry, TrustKernel(audit=audit), approve=approve)
    registry = ledger_registry(
        registry, TaintLedger(), audit=audit, narrow_on_taint=True, approve=approve
    )
    _log.info("governance %s on %s", resolved, surface or "(unnamed surface)")
    return registry, approvals
