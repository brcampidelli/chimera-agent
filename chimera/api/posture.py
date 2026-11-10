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
#: Whether a command on this machine runs when the project's grant allows commands ("granted"), or
#: stops for the owner before each one ("asks"). A third axis would be a mechanism of its own; this is
#: the existing host-exec confirm, exposed on the posture so a turn can ask for it. It only narrows.
Commands = Literal["granted", "asks"]

DEFAULT_REACH: Reach = "workspace"
DEFAULT_APPROVAL: Approval = "suspicious"
DEFAULT_COMMANDS: Commands = "granted"


class Posture(BaseModel):
    """The two axes, as a client sends them, and the one question a mode needs beyond them."""

    reach: Reach = DEFAULT_REACH
    approval: Approval = DEFAULT_APPROVAL
    commands: Commands = DEFAULT_COMMANDS


def commands_run_without_asking(posture: Posture | None, allow_host_exec: bool) -> bool:
    """Whether a host command runs with no confirm, given the turn's grant and its posture.

    ``allow_host_exec`` is what the request asked for. ``commands="asks"`` overrides it: a turn
    that stops before every command must not be ungated because its project granted commands, or
    the mode that promised a question would quietly stop asking. Absent a posture, the request
    decides exactly as it always has.
    """
    if posture is not None and posture.commands == "asks":
        return False
    return allow_host_exec


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


def deployment_fence(settings: Settings) -> tuple[frozenset[str], frozenset[str] | None]:
    """``(denied, allowed)`` as this deployment states them, for a proxy to carry.

    The denials are the union of ``CHIMERA_TOOL_DENYLIST`` and the reach floor; the allowlist is
    ``CHIMERA_TOOL_ALLOWLIST``, ``None`` when it states none. One function, because the deferral
    proxies are registered where the restriction filter cannot see the names behind them, and each
    of them has to be handed these lists explicitly — the chat surfaces were assembling the MCP proxy
    with no list at all, so a denied server tool stayed reachable through ``mcp_call``.

    The owner's deployment policy (``tool.use`` rules in ``policy.json``) is deliberately NOT folded
    in here. These lists are matched by exact name, and the policy is ordered globs where a later
    ``allow`` undoes an earlier ``deny`` of the same file: flattening it to names would either drop
    the globs or keep a deny the file itself undid. Every proxy that holds these lists also asks
    :func:`chimera.deployment_policy.tool_denied` per name, and :meth:`ToolRegistry.run` refuses a
    denied name on every path, so the policy's answer is applied in its own terms, beside — never
    instead of — this fence.
    """
    denied = frozenset({*settings.tool_denylist, *deployment_posture(settings).deny_tools})
    allowed = frozenset(settings.tool_allowlist) if settings.tool_allowlist else None
    return denied, allowed


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


def chat_guard_denials() -> list[str]:
    """The tools :func:`guard_chat_registry` takes away from the app's chat, by name.

    A function of its own because a second reader needs the SAME list, and needs it before the guard
    runs: with ``CHIMERA_DEFER_TOOLS`` on, the chat registry is deferred before this guard, so
    ``execute_code`` and ``code_interpreter`` are already behind ``tool_call`` when the guard matches
    names — it removed ``run_shell`` and left the other two reachable through the proxy. The
    deferral is handed this list so its catalogue refuses them too. Two copies of "what the chat
    guard denies" would agree until the day one of them changed.
    """
    return resolve(Posture(reach=DEFAULT_REACH, approval=DEFAULT_APPROVAL)).deny_tools


def guard_chat_registry(registry: Any, *, audit: Any = None, approve: Any = None) -> tuple[Any, Any]:
    """Apply the coding turn's protections to the CHAT registry — deny by posture, then the ledger.

    The chat and the coding turn talk to the same agent over the same base tools, and until this
    existed only one of them was protected. `chat_stream` applied no write region, no denylist, no
    registry restriction and no taint ledger; the coding turn applied all four. So the chat kept the
    three execution tools the coding turn removes, and none of the tools that refuse once a run has
    read untrusted content. Ask the chat to summarise a page carrying a planted instruction and
    nothing stops it from writing the file that instruction names.

    ``approve`` is who says yes when the narrowing wants a person, and it is the argument this
    function spent its whole life without. Of the seven `ledger_registry` call sites in the
    package, five passed one and two did not: this, and `solve_batch`
    (`chimera/cli/main.py:4716`) — where nobody is at an unattended batch, so the refusal is the
    answer. Here somebody IS at a screen, and omitting it meant `LedgeredTool` had nobody to ask;
    `LedgeredTool` reads *nobody* as *refuse*. Measured on the shipped bench, the same corpus as
    every other arm: the guard blocks
    7 of 7 attacks either way, and the over-block on legitimate work is **0.750 with no approver
    against 0.250 with one** — four questions, all four granted
    (`bench/right_hand_governance/RESULTS.md`, §5b). Two thirds of the price of turning this
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
    denied = chat_guard_denials()
    if denied:
        registry = restrict_registry(registry, allow=None, deny=denied)
    # This used to read "the mode travels; the instruction cannot", and that sentence is why
    # `CHIMERA_TAINT_AUTHORITY` did nothing on this surface for as long as it existed: a ledger
    # nobody tells an instruction answers `unknown` for every fetch, and the narrowing treats
    # `unknown` exactly as it treats `agent`, so the mode had nothing to be a mode ABOUT.
    #
    # It was true of this function and false of the surface. The registry does serve a whole chat —
    # but the ledger it returns is a live object, and the caller sees every turn. `chimera chat`
    # proved it by doing exactly that (`RightHand.begin_turn`), and `_run_app_server` now hands
    # `ChatSession.on_turn_start` a callback into the ledger below. Measured, same instrument as the
    # terminal's: 0 rows moved under `authority` before, 6 after
    # (`bench/right_hand_governance/RESULTS.md`, the `app_chat` columns).
    #
    # A caller that does NOT tell it keeps the old behaviour exactly, which is what makes this safe
    # for the messaging gateway and `/v1/chat/completions` sharing this registry.
    ledger = TaintLedger(
        authority=get_settings().taint_authority,
        egress_allow=get_settings().egress_allow.split(","),
        exfil_host_path=get_settings().exfil_host_path,
        shell_fetch_guard=get_settings().shell_fetch_guard,
        rope_lite=get_settings().taint_rope_lite,
    )
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
    #
    # The owner's lifecycle hooks (`docs/hooks-threat-model.md`), the same as every assembly that goes
    # through `govern_step`: this chat installs no kernel, so it never reaches that function, and
    # without this line the app's chat — the screen a person is sitting at — would be the one surface
    # where the owner's hooks silently did not run. Inside the ledger, and asking the same person.
    from chimera.governance.hooks import apply_hooks

    registry = apply_hooks(
        registry, settings=get_settings(), audit=audit, approve=approve, taint=ledger.record_fetch
    )
    return ledger_registry(
        registry, ledger, narrow_on_taint=resolved.narrow_on_taint, audit=audit, approve=approve,
        rope_lite=get_settings().taint_rope_lite,
    ), ledger


#: The permission modes a conversation can be in, as the Code screen names them. ``bypass`` is the
#: owner's switch (``Ignore permissions``), read from the stored switch, never a mode a conversation
#: stores or a bridge may choose.
PermissionMode = Literal["manual", "accept_edits", "plan", "auto", "dont_ask", "bypass"]

#: Every mode, in the order the selector lists them. Restrictiveness is NOT a rank over this list: a
#: mode is not "above" another, it sets four things (reach, approval, commands, the plan gate), and
#: ``Accept edits`` is narrower than ``Auto`` on two of them and WIDER on reach (it mounts the shell).
#: What a paired device may choose is decided axis by axis in :func:`widens`.
ALL_MODES: tuple[str, ...] = ("manual", "accept_edits", "plan", "auto", "dont_ask", "bypass")

#: The modes a conversation may STORE. ``bypass`` is the owner's switch, never a stored mode.
STORABLE_MODES: frozenset[str] = frozenset(ALL_MODES) - {"bypass"}

_REACH_RANK: dict[str, int] = {"read_only": 0, "workspace": 1, "workspace_shell": 2}
_APPROVAL_RANK: dict[str, int] = {"always": 0, "suspicious": 1, "never": 2}
_COMMANDS_RANK: dict[str, int] = {"asks": 0, "granted": 1}


class ModeTurn(BaseModel):
    """What one permission mode means for a turn: the posture, and whether the plan gate is forced."""

    posture: Posture
    plan_gate: bool = False


def mode_for_approval(approval: Approval) -> PermissionMode:
    """The mode a conversation takes when nothing is stored, read from the owner's Settings approval.

    ``always`` is Manual, ``suspicious`` is Auto and ``never`` is Don't ask: each Settings value keeps
    the posture it always had, so nobody's configured behaviour moves when the selector appears.
    """
    if approval == "always":
        return "manual"
    if approval == "never":
        return "dont_ask"
    return "auto"


def mode_turn(mode: PermissionMode, base_reach: Reach, base_approval: Approval) -> ModeTurn:
    """The posture one turn runs under for ``mode``, over the configured reach and approval.

    The same table the Code screen applies (``apps/desktop/src/lib/permission-modes.ts``), held to the
    same fixture by ``tests/test_permission_modes_table.py``. A bridge turn is built here, on the
    server, so the bridge cannot pick a posture the table does not give.
    """
    if mode == "manual":
        return ModeTurn(posture=Posture(reach=base_reach, approval="always", commands="granted"))
    if mode == "accept_edits":
        reach: Reach = "read_only" if base_reach == "read_only" else "workspace_shell"
        return ModeTurn(posture=Posture(reach=reach, approval="never", commands="asks"))
    if mode == "plan":
        return ModeTurn(
            posture=Posture(reach=base_reach, approval=min(base_approval, "suspicious", key=_APPROVAL_RANK.__getitem__), commands="granted"),
            plan_gate=True,
        )
    if mode == "auto":
        return ModeTurn(posture=Posture(reach=base_reach, approval="suspicious", commands="granted"))
    if mode == "dont_ask":
        return ModeTurn(posture=Posture(reach=base_reach, approval="never", commands="granted"))
    return ModeTurn(posture=Posture(reach=base_reach, approval=base_approval, commands="granted"))


def narrow_posture(a: Posture, b: Posture) -> Posture:
    """The posture that is the more restrictive of the two on every axis, axis by axis.

    Reach: the lower; approval: the one that stops sooner; commands: ``asks`` wins. Never wider than
    either argument, so a posture a device sent can only take something away from the mode's.
    """
    return Posture(
        reach=min(a.reach, b.reach, key=_REACH_RANK.__getitem__),
        approval=min(a.approval, b.approval, key=_APPROVAL_RANK.__getitem__),
        commands=min(a.commands, b.commands, key=_COMMANDS_RANK.__getitem__),
    )


def widens(candidate: ModeTurn, reference: ModeTurn) -> bool:
    """Whether ``candidate`` lets the agent do more than ``reference`` on ANY axis.

    Four axes: reach, approval, commands and the plan gate (a gate the reference forces and the
    candidate drops is wider). One axis wider is enough: a mode is not wider because it is narrower
    somewhere else.
    """
    c, r = candidate.posture, reference.posture
    return (
        _REACH_RANK[c.reach] > _REACH_RANK[r.reach]
        or _APPROVAL_RANK[c.approval] > _APPROVAL_RANK[r.approval]
        or _COMMANDS_RANK[c.commands] > _COMMANDS_RANK[r.commands]
        or (reference.plan_gate and not candidate.plan_gate)
    )


def bridge_mode(
    requested: str | None,
    current: PermissionMode,
    base_reach: Reach = DEFAULT_REACH,
    base_approval: Approval = DEFAULT_APPROVAL,
) -> PermissionMode:
    """The mode a bridge turn runs under, given what it asked for and the conversation's current mode.

    The bridge gets what it asked for only when that mode widens nothing against the current one, on
    any of the four axes (:func:`widens`). Anything wider, anything unknown, ``bypass`` and no request
    at all leave the current mode in force: a paired device can narrow a conversation, never widen it.
    """
    if requested is None or requested == "bypass" or requested not in STORABLE_MODES:
        return current
    asked = cast("PermissionMode", requested)
    if widens(mode_turn(asked, base_reach, base_approval), mode_turn(current, base_reach, base_approval)):
        return current
    return asked


def bridge_turn(
    requested: str | None,
    current: PermissionMode,
    base_reach: Reach,
    base_approval: Approval,
    sent: Posture | None = None,
) -> tuple[PermissionMode, ModeTurn]:
    """The mode and posture a paired device's turn runs under.

    :func:`bridge_mode` picks the mode; :func:`mode_turn` gives its posture over the CONFIGURED reach
    and approval (never the device's); and a posture the device sent narrows it further, axis by axis
    (:func:`narrow_posture`). What the device sent can take away, never add: the old replacement of the
    sent posture by the mode's could widen a ``{always, asks}`` into ``{suspicious, granted}``.
    """
    chosen = bridge_mode(requested, current, base_reach, base_approval)
    turn = mode_turn(chosen, base_reach, base_approval)
    if sent is not None:
        turn = ModeTurn(posture=narrow_posture(turn.posture, sent), plan_gate=turn.plan_gate)
    return chosen, turn
