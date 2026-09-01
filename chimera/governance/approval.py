"""Who says yes — and, when nobody can, how the refusal gets heard.

Both governance layers have taken an ``approve=`` callable since they were written, and neither has
ever been given one. The consequence is measured rather than argued: with no approver, a run that
reads anything external has **100%** of its dangerous-class calls refused
(`bench/injection/PREREGISTRATION.md`). The gate was never too strict — there was simply nothing on
the other side of it.

**The failure this module is really built against is not the refusal, it is the silence.** A refused
call returns an ordinary observation string, so the agent reads "[taint: needs review]" like any
other tool result and carries on. The run finishes, the answer is prose, and the receipt says
success. On the 24/7 path that means a position guardian reporting green while it guarded nothing —
and that is indistinguishable, from outside, from a daemon that stopped.

So every refusal is COUNTED, on the run, wherever it happens. An approver that denies loudly is a
policy decision; an approver that denies invisibly is a bug with a configuration file.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from chimera.telemetry import get_logger

_log = get_logger("governance.approval")


class _HasReason(Protocol):
    reason: str


@dataclass
class ApprovalLedger:
    """Every refusal and grant on one run, so a job can be asked how much it was allowed to do.

    A list rather than a counter: "three writes were refused" is a different sentence from "three
    writes were refused, all of them to the same file", and the second is the one that tells you
    whether the run was blocked or merely nudged.
    """

    refused: list[str] = field(default_factory=list)
    granted: list[str] = field(default_factory=list)

    def record(self, action: str, approved: bool) -> None:
        (self.granted if approved else self.refused).append(action[:200])

    @property
    def blocked(self) -> bool:
        """True when anything at all was refused. The signal a caller must not be able to miss."""
        return bool(self.refused)

    def summary(self) -> str:
        if not self.refused:
            return ""
        return f"{len(self.refused)} action(s) refused for review: " + "; ".join(
            sorted(set(self.refused))[:5]
        )


#: The two layers ask in two shapes: the kernel passes ``(verdict, action)`` and the taint ledger
#: passes ``(assessment,)``. One adapter takes both rather than two near-identical approvers that
#: drift apart — the reason a caller can wire the same policy into both.
Approver = Callable[..., bool]


def _describe(*args: Any) -> tuple[str, str]:
    """(action, reason) out of either call shape."""
    if len(args) == 2:
        verdict, action = args
        return str(action), str(getattr(verdict, "reason", "") or "")
    assessment = args[0] if args else None
    return "", str(getattr(assessment, "reason", "") or "")


def deny(ledger: ApprovalLedger | None = None) -> Approver:
    """Refuse everything, and say so — the honest headless default.

    Unattended, there is no one to ask, and inventing consent is the one answer that must never be
    available. What changes versus having no approver at all is that the refusal is recorded, so the
    caller can tell "the job did its work" from "the job was not allowed to".
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        if ledger is not None:
            ledger.record(action or reason, approved=False)
        _log.warning("refused (no approver available): %s", reason or action)
        return False

    return approve


def allow(ledger: ApprovalLedger | None = None) -> Approver:
    """Grant everything. Opt-in, never a default, and still recorded.

    For a batch on a workspace whose contents the owner already trusts — where the narrowing costs
    more than it buys and that trade has been made deliberately. The record is what keeps it from
    becoming invisible: a run that was allowed 40 dangerous actions should be able to say so.
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        if ledger is not None:
            ledger.record(action or reason, approved=True)
        return True

    return approve


def ask(ledger: ApprovalLedger | None = None, *, stream: Any = None) -> Approver:
    """Prompt a person. Anything other than an explicit yes is a no.

    Default-deny on EOF, on a closed pipe, and on an unreadable answer — a prompt that treats
    silence as consent is worse than no prompt, because it produces a record of an approval nobody
    gave.
    """

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        out = stream or sys.stderr
        try:
            print(f"\n[governance] {reason or 'review required'}", file=out)
            if action:
                print(f"  action: {action[:300]}", file=out)
            print("  allow this once? [y/N] ", end="", file=out, flush=True)
            answer = input().strip().lower()
        except (EOFError, OSError, KeyboardInterrupt):
            answer = ""
        approved = answer in ("y", "yes")
        if ledger is not None:
            ledger.record(action or reason, approved=approved)
        return approved

    return approve


def approver_for(
    mode: str,
    ledger: ApprovalLedger | None = None,
    *,
    home: Any = None,
    deliver: Any = None,
) -> Approver:
    """Build the approver for a configured mode: ``ask`` | ``deny`` | ``allow``.

    ``ask`` degrades to ``deny`` with no terminal attached, which is what a cron job has. Degrading
    the other way — falling back to allow because nobody could be asked — would turn an unattended
    deployment into the most permissive configuration in the product, which is exactly backwards.

    ``home`` opts into asking somebody who is NOT at the keyboard. Without a terminal the three-state
    gate used to collapse into two — every REVIEW became a refusal — so the mandate that says
    "confirm before billing, before a destructive migration, before touching RLS" had nothing to
    confirm with. With a home (and ideally a ``deliver``), the question is written down, sent
    wherever this deployment sends things, and answered with ``chimera approve``. Silence still
    refuses; see :mod:`chimera.governance.pending`.
    """
    mode = (mode or "ask").strip().lower()
    if mode == "allow":
        return allow(ledger)
    if mode == "deny":
        return deny(ledger)
    if not sys.stdin or not sys.stdin.isatty():
        if home is not None:
            return ask_elsewhere(home, ledger, deliver=deliver)
        _log.info("approval mode 'ask' with no terminal: denying and recording")
        return deny(ledger)
    return ask(ledger)


def ask_elsewhere(home: Any, ledger: ApprovalLedger | None = None, *, deliver: Any = None) -> Approver:
    """Ask a person who is elsewhere, and wait. Anything but an explicit yes is a no."""
    from chimera.governance.pending import ask_durably

    def approve(*args: Any) -> bool:
        action, reason = _describe(*args)
        approved = ask_durably(home, action, reason, deliver=deliver)
        if ledger is not None:
            ledger.record(action or reason, approved=approved)
        return approved

    return approve
