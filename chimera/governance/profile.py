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

from typing import TYPE_CHECKING, Any, NamedTuple

from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from chimera.config import Settings
    from chimera.governance.audit import AuditLog

_log = get_logger("governance.profile")

#: What a surface asked for, so the answer can be audited later. Not a bool: "this surface has no
#: governance" and "this surface was assembled in observe mode" are different states and the
#: distinction is what the rollout is about.
MODES = ("off", "observe", "enforce")


class GovernanceStep(NamedTuple):
    """What the deployment's governance decided, and the registry it produced."""

    registry: Any
    approvals: Any
    """The record of what was refused or granted. Empty when ``mode`` is ``"off"``."""
    approve: Any | None
    """The approver the kernel was given. ``None`` when governance is off — which is NOT the same as
    the ``approve=None`` a wrapper reads as "nobody can approve, so refuse". A caller passing this
    on to another layer must check ``mode`` first."""
    mode: str


def govern_step(
    registry: Any,
    *,
    settings: Settings,
    audit: AuditLog,
    mode: str = "",
    surface: str = "",
    attended: bool = True,
    audit_allows: bool = True,
) -> GovernanceStep:
    """Apply the deployment's trust kernel to ``registry``, under the deployment's mode.

    Split out of :func:`governed_profile` for the one caller that could not use the whole thing. The
    API's ``assemble_registry`` builds its own write region, its own union of posture and denylist,
    and its own taint ledger **which it hands back to the caller** — so wrapping the profile around
    that assembly would have produced a SECOND ``TaintLedger``, outermost, watching the tools while
    the caller held the inner one. ``assemble_registry`` already says why that is fatal: "the run
    that got tainted and the run that gets asked about it would be different objects, and the pause
    would never fire." (An earlier draft of this paragraph also claimed a duplicated
    ``restrict_registry`` would spam the audit. That was wrong and is recorded here rather than
    deleted: ``assemble_registry`` calls it with no ``audit`` at all, and it writes one line per
    construction, not per call. The ledger is the whole reason.)

    So the kernel — the one layer that path genuinely lacked — moves here, and both callers read the
    mode off the same lines. Two copies of "what does ``enfroce`` mean" is how one of them ends up
    answering differently.

    ``attended`` is the API's reason for this being a parameter at all. ``approver_for("ask")``
    prompts on **the server's** stdin, and degrades to deny only when that stdin is not a terminal —
    so a ``chimera serve`` started from a shell would, under ``enforce``, stop an HTTP request and
    wait for whoever happens to be looking at the console to type ``y``. That person did not make
    the request and cannot see what it was for, and the worker blocks until they answer. An approval
    means something only when the person answering is the person who asked, which on this surface is
    never — so an unattended caller passes ``attended=False`` and the prompt is never built.
    """
    from chimera.governance import ApprovalLedger, TrustKernel
    from chimera.governance.approval import allow as allow_everything
    from chimera.governance.approval import approver_for
    from chimera.governance.governed_tool import govern_registry

    resolved = (mode or settings.governance_mode or "off").strip().lower()
    if resolved not in MODES:
        _log.warning("unknown governance mode %r on %s: treating as 'off'", resolved, surface)
        resolved = "off"

    approvals = ApprovalLedger()
    if resolved == "off":
        return GovernanceStep(registry, approvals, None, resolved)

    # In observe the approver says yes to everything and writes down that it did. Every call that
    # reaches an approver is one the policy WOULD have refused, so `approvals.granted` is exactly
    # the report a rollout needs: what enforcement would cost, per surface, measured not guessed.
    if resolved == "observe":
        approve = allow_everything(approvals)
    else:
        wanted = settings.approval_mode
        if not attended and wanted == "ask":
            # `deny` is what `approver_for` picks for itself once it finds no terminal; this reaches
            # the same fail-closed answer one step earlier, before a tty that belongs to somebody
            # else can be mistaken for the requester.
            wanted = "deny"
        approve = approver_for(wanted, approvals)

    registry = govern_registry(
        registry, TrustKernel(audit=audit, audit_allows=audit_allows), approve=approve
    )
    _log.info("governance %s on %s", resolved, surface or "(unnamed surface)")
    return GovernanceStep(registry, approvals, approve, resolved)


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
    from chimera.governance import TaintLedger, restrict_registry
    from chimera.governance.audit import AuditLog
    from chimera.governance.ledger_tool import ledger_registry

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

    # --- the inferential machinery: still staged behind observe/enforce ---------------------------
    #
    # The mode resolution and the kernel now live in `govern_step`, which the API's own assembly
    # calls too. They were written out here and nowhere else, which is why for a long time the kernel
    # reached `chimera run --guard` and the cron and nothing served over HTTP.
    step = govern_step(registry, settings=settings, audit=audit, mode=mode, surface=surface)
    if step.mode == "off":
        return step.registry, step.approvals

    registry = ledger_registry(
        step.registry, TaintLedger(), audit=audit, narrow_on_taint=True, approve=step.approve
    )
    return registry, step.approvals
