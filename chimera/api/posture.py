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


#: Why the shell would run on this machine. "" when it would not.
FellBackReason = Literal["", "no_container", "no_os_sandbox"]


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
    #: WHY the fall-back happened, because there are now two reasons and they need different
    #: sentences. ``"no_container"`` is the original: a Docker sandbox was configured and no daemon
    #: answered, so "start Docker" is advice that works. ``"no_os_sandbox"`` is the `auto` default on
    #: a machine with no kernel mechanism — Windows, or a Linux that refuses unprivileged user
    #: namespaces — where starting Docker is not what the user set up and telling them a container
    #: was configured is simply false. The screen was saying the container sentence in both cases,
    #: which reached the right conclusion by the wrong reason and sent people to fix the wrong thing.
    fell_back_reason: FellBackReason = ""
    #: True when this surface has NO taint ledger — nothing marks the run after it reads untrusted
    #: content, so the tools that would otherwise start refusing keep working.
    #:
    #: This was written when the permissive assembly was the DEFAULT, and it said so: a permissive
    #: default that says nothing is the one version of that choice which cannot be defended. Since
    #: 2026-09-10 ``CHIMERA_GUARD_CHAT`` defaults to on, so the field now reports the opposite
    #: direction — a user who turned the guard OFF, and who is owed the same honesty for the same
    #: reason. The coding turn is always guarded; a chat is guarded unless it was disarmed.
    unguarded: bool = False
    #: The external agent doing the work, or "" for Chimera's own loop.
    #:
    #: When set, everything above changes meaning and the interface has to say so. An ACP agent has
    #: file and shell tools of its own: it MAY route a write through our handler, where the write
    #: region and the workspace jail apply exactly as they do natively — and it may not, in which
    #: case they apply to nothing. So `writes` and `shell` stop being boundaries and become
    #: descriptions of the calls we happen to see.
    #:
    #: What survives intact is the checkpoint: the workspace is snapshotted before the turn and can
    #: be put back afterwards, whatever the agent used to change it. That is a real guarantee and a
    #: smaller one, and stating the smaller one is the whole reason this field exists. A posture
    #: sentence that claimed prevention here would be the one lie this product cannot afford.
    external_agent: str = ""


def describe(
    posture: Posture,
    workspace: Path,
    settings: Settings,
    *,
    can_pause: bool = True,
    guarded: bool = True,
    external_agent: str = "",
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
    fell_back_reason: FellBackReason = ""
    if not (EXEC_TOOLS & set(resolved.deny_tools)):
        isolated = False
        try:
            isolated = sandbox_is_isolated(get_sandbox())
        except Exception:  # noqa: BLE001 — an unbuildable sandbox is a host sandbox, the safe read
            isolated = False
        if isolated:
            shell = "isolated"
        else:
            # Anything but an explicit `local` asked for a boundary and did not get one. Under the
            # `auto` default this is how a Windows user — where no OS sandbox exists — is told that
            # their commands run on the host, on every posture read rather than once at startup.
            configured = (settings.sandbox or "auto").lower()
            fell_back = configured != "local"
            # A container was named and did not answer, versus no kernel mechanism exists here at
            # all. Both end on this machine; only one of them is fixed by starting a daemon.
            fell_back_reason = (
                "no_container" if configured in {"docker", "container", "podman"}
                else "no_os_sandbox" if fell_back
                else ""
            )
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
        fell_back_reason=fell_back_reason,
        unguarded=not guarded,
        external_agent=external_agent,
    )


def guard_chat_registry(registry: Any, *, audit: Any = None, approve: Any = None) -> tuple[Any, Any]:
    """Apply the coding turn's protections to the CHAT registry — deny by posture, then the ledger.

    The chat and the coding turn talk to the same agent over the same base tools, and until this
    existed only one of them was protected. `chat_stream` applied no write region, no denylist, no
    registry restriction and no taint ledger; the coding turn applied all four. So the chat kept the
    three execution tools the coding turn removes, and none of the tools that refuse once a run has
    read untrusted content. Ask the chat to summarise a page carrying a planted instruction and
    nothing stops it from writing the file that instruction names.

    ``approve`` is who says yes when the narrowing wants a person, and it is the argument this
    function spent its whole life without. Every other `ledger_registry` caller passes one;
    omitting it here meant `LedgeredTool` had nobody to ask, and `LedgeredTool` reads *nobody* as
    *refuse*. Measured on the shipped bench, the same corpus as every other arm: the guard blocks
    7 of 7 attacks either way, and the over-block on legitimate work is **0.750 with no approver
    against 0.250 with one** — four questions, all four granted
    (`bench/right_hand_governance/RESULTS.md`, §5b). Three quarters of the price of turning this
    guard on was never the guard. It was the silence behind it.

    ``None`` keeps the old behaviour exactly, so a caller that has nobody to ask — a batch, a test,
    a harness — is unchanged.

    **This used to say the registry is shared "with the messaging gateway and
    ``/v1/chat/completions``", and the messaging half was false.** `MessagingManager` builds its own
    sessions through `governed_profile(..., surface="app-messaging")`
    (`chimera/server/manager.py:139-147`) and has never seen this function. The OpenAI half was
    true and is no longer: `build_api_app` takes a second factory for that endpoint, so the two
    surfaces can be assembled differently — which is what let ``CHIMERA_GUARD_CHAT`` default to on
    for the screen a person is sitting at without arming a benchmark harness that cannot answer.

    Deliberately takes an ALREADY-BUILT registry rather than building one: the chat's registry
    carries MCP tools that a from-scratch build would drop, and applying the denylist here means it
    reaches those tools too. A guard that covers only the tools we happened to write is not a guard.
    """
    from chimera.config import get_settings
    from chimera.governance import TaintLedger, ledger_registry, restrict_registry

    resolved = resolve(Posture(reach=DEFAULT_REACH, approval=DEFAULT_APPROVAL))
    if resolved.deny_tools:
        registry = restrict_registry(registry, allow=None, deny=resolved.deny_tools)
    # This used to read "the mode travels; the instruction cannot", and that sentence is why
    # `CHIMERA_TAINT_AUTHORITY` did nothing on this surface for as long as it existed: a ledger
    # nobody tells an instruction answers `unknown` for every fetch, and the narrowing treats
    # `unknown` exactly as it treats `agent`, so the mode had nothing to be a mode ABOUT.
    #
    # It was true of this function and false of the surface. The registry does serve a whole chat —
    # but the ledger it returns is a live object, and the caller sees every turn. `chimera chat`
    # proved it by doing exactly that (`RightHand.begin_turn`), and `desktop_app` now hands
    # `ChatSession.on_turn_start` a callback into the ledger below. Measured, same instrument as the
    # terminal's: 0 rows moved under `authority` before, 6 after
    # (`bench/right_hand_governance/RESULTS.md`, the `app_chat` columns).
    #
    # A caller that does NOT tell it keeps the old behaviour exactly, which is what makes this safe
    # for the messaging gateway and `/v1/chat/completions` sharing this registry.
    ledger = TaintLedger(authority=get_settings().taint_authority)
    # The audit log, which this was the ONE `ledger_registry` caller not passing. Both siblings do
    # — `code_api` and `governed_profile` — and every write inside `LedgeredTool` is guarded by
    # `if self.audit is not None`, so omitting it meant this guard recorded nothing at all.
    #
    # The consequence was not a missing log line, it was a false statement: the Governance screen's
    # empty state says "the app records an entry whenever a defence fires", so turning this guard on
    # and having it narrow or refuse a call left the screen reporting that nothing had been narrowed.
    # That is the exact reassurance the sentence was written to prevent.
    #
    # Not passing `audit` from `restrict_registry` above, for the reason `code_api` gives at the
    # same spot: the posture excludes the exec tools on every turn, so that would append an
    # identical entry per turn and bury the rare events someone opens this log to find.
    return ledger_registry(
        registry, ledger, narrow_on_taint=resolved.narrow_on_taint, audit=audit, approve=approve
    ), ledger
