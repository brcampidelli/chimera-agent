"""The permission mode of a scheduled job: a ceiling that is never above the install's own.

The Code screen has five modes (``chimera/api/posture.py``) and each means a posture: how far the
agent reaches, when it stops to ask, whether a command or an edit is a question. A scheduled run has
the same needs and one fact the Code screen does not: **nobody is there**. Two things follow, and
both are decisions rather than conveniences.

**The mode is a ceiling, and the ceiling is the install's.** A job is written once and runs for
months, so a mode wider than Settings would be reach the owner handed out at one moment and could
not see being used at 3 a.m. :func:`refusal_for` is what the owner's screen asks before saving one
(it answers with a sentence when the mode is wider than the global posture on ANY axis, the same
test a paired device meets in ``bridge_mode``), and :func:`resolve` narrows again at run time, axis
by axis, because Settings can be tightened after the job was written. ``bypass`` is not a mode a job
can name at all, and the job never inherits the owner's switch.

**A mode that would ask refuses instead.** ``manual`` asks before every edit and command and
``accept_edits`` before every command. On the Code screen the question goes on a card; here there is
no card and no one to read it, and a run that waited would be a timeout with a bill, while one that
answered for itself would be the one thing an approval exists to prevent. So the tools that would
ask are not in the registry (:attr:`JobPermission.deny`), and the run is told so in one sentence
(:attr:`JobPermission.note`) instead of discovering it call by call. This is how the Code screen
already treats a turn with no screen (``manual_gate.NEEDS_SCREEN``): the same refusal, reached by
removing the tool because this assembly goes through ``governed_profile``, which takes a deny list
and nothing finer.

``plan`` stops for a person on the plan before anything runs. A scheduled run has no one to approve
a plan, so it is read as the strictest thing it can honestly be: a run that reads and answers and
writes nothing and runs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from chimera.api.posture import (
    DEFAULT_APPROVAL,
    DEFAULT_REACH,
    STORABLE_MODES,
    Approval,
    ModeTurn,
    PermissionMode,
    Reach,
    mode_for_approval,
    mode_turn,
    narrow_posture,
    widens,
)
from chimera.api.posture import resolve as resolve_posture
from chimera.governance.ledger import EXEC_TOOLS, WRITE_TOOLS
from chimera.scheduler.models import JOB_PERMISSION_MODES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

_REACHES = ("read_only", "workspace", "workspace_shell")
_APPROVALS = ("always", "suspicious", "never")


@dataclass(frozen=True)
class JobPermission:
    """What a job's mode means for one run."""

    mode: str
    deny: tuple[str, ...]
    """Tool names removed from the run's registry."""
    narrowed: bool
    """The mode was wider than the install's posture is NOW, and the run took the narrower."""
    note: str
    """One sentence for the run's turn context: what it may not do, and why."""


def global_mode(settings: Settings) -> PermissionMode:
    """The mode the install's own Settings amount to: what a Code conversation with no stored mode
    runs under. A value Settings do not know reads as the default, as ``posture`` does."""
    approval = (settings.approval or "").strip()
    return mode_for_approval(cast(Approval, approval if approval in _APPROVALS else DEFAULT_APPROVAL))


def _base(settings: Settings) -> tuple[Reach, Approval]:
    reach = (settings.reach or "").strip()
    approval = (settings.approval or "").strip()
    return (
        cast(Reach, reach if reach in _REACHES else DEFAULT_REACH),
        cast(Approval, approval if approval in _APPROVALS else DEFAULT_APPROVAL),
    )


def global_turn(settings: Settings) -> ModeTurn:
    reach, approval = _base(settings)
    return mode_turn(global_mode(settings), reach, approval)


def valid_mode(mode: str) -> bool:
    """A mode a job may name: one of the Code screen's, never ``bypass``."""
    return mode in JOB_PERMISSION_MODES and mode in STORABLE_MODES


def refusal_for(mode: str, settings: Settings) -> str | None:
    """Why ``mode`` may not be given to a job under these Settings, or None when it may.

    Refused: a mode that does not exist or is ``bypass``, and a mode that lets the agent do more
    than the install's own posture on any of reach, approval, commands, edits or the plan gate.
    """
    if not mode:
        return None
    if mode == "bypass":
        return "a scheduled task never runs with permissions ignored: nobody is watching it"
    if not valid_mode(mode):
        return f"unknown permission mode {mode!r}"
    reach, approval = _base(settings)
    if widens(mode_turn(cast(PermissionMode, mode), reach, approval), global_turn(settings)):
        return (
            f"the {mode} mode lets a task do more than your Settings allow "
            f"({global_mode(settings)}); a scheduled task may be equal or stricter, never wider"
        )
    return None


def resolve(mode: str, settings: Settings) -> JobPermission | None:
    """The permission for one run, or None when the job names no mode (it runs as it always did).

    Raises ``ValueError`` for a mode no job may hold (``bypass``, or a word that is not a mode): a
    ``jobs.json`` edited by hand must not be able to hand an unattended run the one switch that has
    no one to catch its mistake, and quietly running it with no mode instead would be the same
    thing under another name.
    """
    if not mode:
        return None
    if not valid_mode(mode):
        raise ValueError(f"permission_mode {mode!r} is not a mode a scheduled task may hold")
    reach, approval = _base(settings)
    asked = mode_turn(cast(PermissionMode, mode), reach, approval)
    base = global_turn(settings)
    narrowed = widens(asked, base)
    posture = narrow_posture(asked.posture, base.posture)
    plan_only = asked.plan_gate or base.plan_gate
    denied: set[str] = set(resolve_posture(posture).deny_tools)
    if plan_only:
        denied |= WRITE_TOOLS | EXEC_TOOLS
    if posture.commands == "asks":
        denied |= EXEC_TOOLS
    if posture.edits == "asks":
        denied |= WRITE_TOOLS
    return JobPermission(
        mode=mode,
        deny=tuple(sorted(denied)),
        narrowed=narrowed,
        note=_note(mode, plan_only, posture.commands == "asks", posture.edits == "asks", denied),
    )


def _note(mode: str, plan_only: bool, no_commands: bool, no_edits: bool, denied: set[str]) -> str:
    """The sentence the run reads. Prompt, not a boundary: the boundary is the missing tools."""
    if not denied:
        return f"Permission mode for this run: {mode}."
    parts = []
    if plan_only:
        parts.append("this run only reads and answers: it may not write files or run commands")
    else:
        if no_edits:
            parts.append("file edits are refused")
        if no_commands:
            parts.append("commands are refused")
        if not parts:
            parts.append("some tools are not available")
    return (
        f"Permission mode for this run: {mode}. Nobody is watching to answer a question, so "
        f"{' and '.join(parts)}; do not look for another way to do them, and say in your answer "
        "what you could not do."
    )
