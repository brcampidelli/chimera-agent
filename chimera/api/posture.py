"""Two axes, not twenty switches: how far the agent can reach, and when it stops to ask.

Chimera already had both halves of this and showed neither. Reach was spread across the tool
registry, the write region, the workspace jail, ``CHIMERA_SANDBOX`` and ``CHIMERA_HOST_EXEC``;
asking was spread across the taint ledger, ``narrow_on_taint`` and the HITL checkpoint. A user could
not see any of it, and the Code screen offered none of it.

The framing is Codex's and it is the right one: **the sandbox decides what is technically possible;
the approval policy decides when to stop and ask before crossing it.** They are orthogonal. Collapse
them into one "safety level" slider and the diagonal — full reach, no questions — stops being a
thing anyone chose and becomes a thing they slid past.

Three decisions worth stating:

**Reach is about the workspace, not about the world.** ``workspace_shell`` does not hand out the
outward-facing tools (mail, webhooks, issue creation); those stay where they were, governed by the
taint ledger, because "may this edit my repo" and "may this email someone" are not the same
question and a control that answered both would be answering neither.

**Every value maps to a mechanism that already refuses things.** Nothing here is advisory. A reach
that removes the write tools removes them from the registry; an approval that pauses uses the same
checkpoint the CLI does. The one gap — "ask me before *every* run", which had no trigger because
the only pause condition was taint — was closed in the loop rather than faked here. A selector whose
third value quietly did the same as its second would be worse than a selector with two values.

**What the UI shows is derived, never echoed back.** :func:`describe` reports what is *true right
now*: it asks the sandbox whether it is actually isolated rather than reading the config that asked
for it, so a Docker daemon that is down turns "runs in a container" into "runs on YOUR machine"
without anyone editing a setting. That is the whole point of generating the sentence.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

#: How far into the workspace the agent may reach.
Reach = Literal["read_only", "workspace", "workspace_shell"]

#: When the run stops and waits for a human.
Approval = Literal["always", "suspicious", "never"]

DEFAULT_REACH: Reach = "workspace"
DEFAULT_APPROVAL: Approval = "suspicious"


class Posture(BaseModel):
    """The two axes, as a client sends them."""

    reach: Reach = DEFAULT_REACH
    approval: Approval = DEFAULT_APPROVAL


@dataclass(frozen=True)
class ResolvedPosture:
    """What the two axes actually do, in the seams the run endpoint already had."""

    deny_tools: list[str]
    pause_on_taint: bool
    pause_always: bool
    narrow_on_taint: bool

    @property
    def needs_thread(self) -> bool:
        """Whether this posture can pause at all. A pause with no durable thread is a run nobody
        can come back to, so the endpoint must mint one before honouring either pause flag."""
        return self.pause_on_taint or self.pause_always


def resolve(posture: Posture) -> ResolvedPosture:
    """Turn the two axes into denials and pause flags.

    Read off the governance sets rather than a hand-kept list of names, so a tool added to
    ``WRITE_TOOLS`` or ``EXEC_TOOLS`` is covered here the day it lands — the alternative is a second
    list that agrees with the first until the day it does not, silently.
    """
    from chimera.governance.ledger import EXEC_TOOLS, WRITE_TOOLS

    denied: set[str] = set()
    if posture.reach == "read_only":
        denied |= WRITE_TOOLS | EXEC_TOOLS
    elif posture.reach == "workspace":
        denied |= EXEC_TOOLS

    return ResolvedPosture(
        deny_tools=sorted(denied),
        pause_on_taint=posture.approval == "suspicious",
        pause_always=posture.approval == "always",
        # Taint-adaptive narrowing rides with "ask if suspicious": both are the same judgement —
        # that untrusted input in the run is a reason to be more careful, not less.
        narrow_on_taint=posture.approval == "suspicious",
    )


def deployment_posture(settings: Settings) -> ResolvedPosture:
    """The posture this deployment states, resolved. Nothing denied when it states none.

    A FLOOR rather than a default, and the difference matters: a default is what a request gets when
    it sends nothing, so any client could step around it by sending something. This unions with
    whatever the request sent, so the owner's answer to "how much may my agent do" survives a client
    that disagrees — the same rule, for the same reason, as CHIMERA_TOOL_DENYLIST.

    Empty strings mean "this deployment states no posture", which is deliberately NOT the same as
    stating a permissive one. Every caller that predates this setting sends no posture and gets
    nothing denied; making the empty value mean ``workspace`` would silently take the shell away from
    all of them, and it would read as the agent having got worse at its job.
    """
    reach = settings.reach.strip()
    approval = settings.approval.strip()
    # Resolved axis by axis, each against the NEUTRAL value of the other, because the two do not
    # leak into each other — that is what makes them axes, and it is asserted a few tests up. Filling
    # an unset axis with its default instead would mean that setting "stop and ask me when something
    # smells wrong" silently takes away the shell, which nobody asked for and nothing would report.
    denied = (
        resolve(Posture(reach=cast("Reach", reach), approval="never")).deny_tools if reach else []
    )
    pauses = (
        resolve(Posture(reach="workspace_shell", approval=cast("Approval", approval)))
        if approval
        else None
    )
    return ResolvedPosture(
        deny_tools=denied,
        pause_on_taint=bool(pauses and pauses.pause_on_taint),
        pause_always=bool(pauses and pauses.pause_always),
        narrow_on_taint=bool(pauses and pauses.narrow_on_taint),
    )


#: Where the agent's writes can land.
Writes = Literal["nothing", "workspace"]
#: Where a shell command would actually execute — the fact, not the configuration.
Shell = Literal["none", "isolated", "host", "asks", "refused"]
#: When the run stops for a human.
Pauses = Literal["always", "tainted", "never"]


class PostureFacts(BaseModel):
    """What is true right now, for the UI to render as one sentence.

    Structured rather than prose because the sentence has to exist in every language the app ships,
    and a server that returned English would quietly make this the one untranslated line on the
    screen.
    """

    writes: Writes
    workspace: str
    shell: Shell
    pauses: Pauses
    #: True when the shell would run on this machine while the configuration asked for a container
    #: — a Docker sandbox that fell back because no daemon answered. Surfaced separately because it
    #: is the one case where the honest answer contradicts what the user set up, and silently
    #: honouring the config here is exactly how "I thought it was sandboxed" happens.
    fell_back_to_host: bool = False
    #: True when this surface has NO taint ledger — nothing marks the run after it reads untrusted
    #: content, so the tools that would otherwise start refusing keep working. Reported because the
    #: default is the permissive one (``CHIMERA_GUARD_CHAT`` is off), and a permissive default that
    #: says nothing is the one version of that choice which cannot be defended. The coding turn is
    #: always guarded; a chat is guarded only if the user turned it on.
    unguarded: bool = False


def describe(
    posture: Posture,
    workspace: Path,
    settings: Settings,
    *,
    can_pause: bool = True,
    guarded: bool = True,
) -> PostureFacts:
    """Report what this posture means on THIS machine, right now.

    Never derived from the config alone: whether the shell is isolated is asked of the sandbox
    object, which knows whether its daemon actually answered.

    ``can_pause`` is the same discipline applied to the pause. The approval axis resolves to
    ``pause_on_taint`` for BOTH surfaces, but only the run wires a checkpointer and a taint ledger:
    ``build_agent`` for a conversational turn passes neither (``chimera/api/code_api.py``), so a turn
    cannot stop and ask no matter what the user selected. Reporting "pauses when tainted" there was
    a sentence about a capability the surface does not have — and this line exists precisely because
    a user should not have to read the source to know what the agent may do to their files.
    """
    resolved = resolve(posture)
    from chimera.governance.ledger import EXEC_TOOLS
    from chimera.sandbox import get_sandbox
    from chimera.sandbox.confirm import sandbox_is_isolated

    shell: Shell = "none"
    fell_back = False
    if not (EXEC_TOOLS & set(resolved.deny_tools)):
        isolated = False
        try:
            isolated = sandbox_is_isolated(get_sandbox())
        except Exception:  # noqa: BLE001 — an unbuildable sandbox is a host sandbox, the safe read
            isolated = False
        if isolated:
            shell = "isolated"
        else:
            fell_back = (settings.sandbox or "local").lower() != "local"
            posture_env = (settings.host_exec or "ask").lower()
            # `ask` is honest about being unanswerable here: the server has no terminal, so the
            # confirm resolves to a refusal rather than to a prompt nobody will ever see.
            shell = {"allow": "host", "deny": "refused"}.get(posture_env, "asks")  # type: ignore[assignment]

    return PostureFacts(
        writes="nothing" if posture.reach == "read_only" else "workspace",
        workspace=str(workspace),
        shell=shell,
        pauses=(
            ("always" if resolved.pause_always else "tainted" if resolved.pause_on_taint else "never")
            if can_pause
            else "never"
        ),
        fell_back_to_host=fell_back,
        unguarded=not guarded,
    )


def guard_chat_registry(registry: Any) -> tuple[Any, Any]:
    """Apply the coding turn's protections to the CHAT registry — deny by posture, then the ledger.

    The chat and the coding turn talk to the same agent over the same base tools, and until this
    existed only one of them was protected. `chat_stream` applied no write region, no denylist, no
    registry restriction and no taint ledger; the coding turn applied all four. So the chat kept the
    three execution tools the coding turn removes, and none of the tools that refuse once a run has
    read untrusted content. Ask the chat to summarise a page carrying a planted instruction and
    nothing stops it from writing the file that instruction names.

    Off by default (``CHIMERA_GUARD_CHAT``), because this registry is shared with the messaging
    gateway and ``/v1/chat/completions``: turning it on by default would silently take shell away
    from agents people already run. Off, the exposure stays — and the posture line says so, which is
    the only thing that makes a permissive default defensible.

    Deliberately takes an ALREADY-BUILT registry rather than building one: the chat's registry
    carries MCP tools that a from-scratch build would drop, and applying the denylist here means it
    reaches those tools too. A guard that covers only the tools we happened to write is not a guard.
    """
    from chimera.governance import TaintLedger, ledger_registry, restrict_registry

    resolved = resolve(Posture(reach=DEFAULT_REACH, approval=DEFAULT_APPROVAL))
    if resolved.deny_tools:
        registry = restrict_registry(registry, allow=None, deny=resolved.deny_tools)
    ledger = TaintLedger()
    return ledger_registry(registry, ledger, narrow_on_taint=resolved.narrow_on_taint), ledger
