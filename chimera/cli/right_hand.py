"""The terminal right-hand's tool stack, assembled once for every surface that is one.

``chimera chat``, ``chimera assist`` and ``chimera tui`` used to build
``_apply_tool_allowlist(default_registry(ws))`` and nothing else, while
``chimera/api/code_api.py:assemble_registry`` built a write region, an allowlist, a trust kernel, a
taint ledger carrying the run's own instruction, and an approver. The gap was not an oversight
anybody had to find — it was written down as three exemptions in
``tests/test_governed_surfaces.py`` whose reason was **"attended"**. All three are gone; the last
one, the TUI's, needed a modal built before it could go.

It was measured before it was argued about (`bench/right_hand_governance/RESULTS.md`, 2026-09-08,
US$ 0): the terminal registry executed **7 of 7** attacks the governed one blocked, and **0 of 12**
external reads came back inside the ``<<external-data>>`` fence that the system prompt `chat` sends
promises in every single turn. That second number is the worse one. A missing defence is a gap; a
prompt that teaches the model to distrust fenced text, on a surface where nothing is ever fenced,
teaches it that everything it reads is trustworthy.

Three decisions worth stating, because each has a plausible-looking alternative.

**Not** :func:`chimera.api.posture.guard_chat_registry`. That function exists, takes a chat registry,
and is the obvious thing to reuse. It resolves ``Posture(reach=DEFAULT_REACH)`` — which denies
``EXEC_TOOLS`` unconditionally — so reusing it would have taken ``run_shell`` away from the one
surface where "ask me before each command" is literally true, and every attack row would then read
BLOCKED because the tool was gone rather than because anything refused it. `RESULTS.md` has a
section on exactly that confusion ("absence is a fence nobody chose and nobody maintains"). It also
hardcodes the posture instead of reading the owner's ``CHIMERA_REACH`` floor, builds no write
region, installs no kernel, sets no instruction and wires no approver — five of the seven things
Step 3 is about. What is left after removing all that is not a reuse.

**The approver PROMPTS.** The API deliberately refuses to wire ``ask`` because a server has nobody
at a console; it writes the question to disk instead. The terminal is the one surface where "nobody
to ask" is false by construction, and it is the only one that built no approver at all — which is
the irony the study named. :func:`chimera.governance.approval.approver_for` already degrades to
``deny`` when stdin is not a tty, so a piped ``chimera chat < script`` keeps today's headless
behaviour and no ``home`` is passed: a durable question nobody will read is a fifteen-minute pause
before the same refusal.

**And where stdin is the wrong pipe, the surface brings its own.** ``chimera tui`` was left out of
all of the above until 2026-09-09, because inside Textual a prompt written to stdin is never read:
measured at 123.8 s to a 120 s timeout, with the question never drawn. It builds this same stack
now, and the difference is one argument — ``ask=`` (:class:`TerminalAsk`), a modal the app draws —
which both gates are routed through. Nothing else about the assembly is per-surface, deliberately:
the three commands differ in the ``surface`` label and in whether they hand over a question, and in
nothing that decides what is allowed.

**The ledger lives as long as the conversation, and the instruction is set per turn.** A fresh
ledger per turn would have been closer to ``assemble_registry`` and would have been wrong here: the
transcript replays the last six turns, so a page fetched on turn 1 is still in the prompt on turn 4,
and a ledger that forgot it would narrow nothing while the poisoned text was still on screen. What
IS per turn is :meth:`TaintLedger.set_instruction` — the user's own words — and that is what makes
``CHIMERA_TAINT_AUTHORITY=authority`` mean something here: a page the person named themselves stops
arming the narrowing, so the cost of this layer falls on fetches nobody asked for. The desktop chat
factory never set it (`chimera/api/posture.py` says so in its own comment: "the mode travels; the
instruction cannot"), which is why that setting is inert there too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings
    from chimera.governance.approval import ApprovalLedger
    from chimera.governance.ledger import TaintLedger


class TerminalAsk(Protocol):
    """A surface's own way of putting a gate's question in front of the person sitting there.

    Two methods rather than one, because the two gates this stack installs are **different
    callables with different signatures** and a surface that answers only one of them keeps the
    other's failure mode intact. That is not hypothetical: it is exactly what `chimera tui` shipped
    between 2026-09-08 and the modal — a host-exec confirm nobody could see, on a surface whose
    taint approver had deliberately never been wired for the same reason.

    A REPL passes nothing here: :func:`chimera.sandbox.confirm._prompt` and
    :func:`chimera.governance.approval.ask` already reach the person through stdin, which is a real
    answer on that surface and a two-minute block on a full-screen one.
    """

    @property
    def attended(self) -> bool:
        """Whether a question asked right now could actually reach a person."""

    def host_exec(self, command: str) -> bool:
        """Run this command on the person's machine, or not."""

    def question(self, action: str, reason: str) -> bool:
        """Allow this governance-flagged action once, or not."""


@dataclass
class RightHand:
    """The governed tool stack one terminal conversation runs on, and the record it keeps."""

    registry: Any
    """What the agent is handed: allowlist, kernel and taint ledger, outermost-last."""

    ledger: TaintLedger
    """The run's taint ledger. Held because :meth:`begin_turn` has to reach it every turn."""

    approvals: ApprovalLedger
    """Every governance question this conversation asked, and how it was answered."""

    workspace: Path

    #: True when an approver that can actually say yes was wired. False under a pipe, where
    #: ``approver_for`` degrades to deny — reported rather than inferred, because "refused" and
    #: "refused because nobody could be asked" are different sentences for the person reading them.
    attended: bool = False

    #: How many verdicts had been recorded when the last turn ended, so `turn_verdicts` is a pure
    #: subtraction. Two counters rather than a slice of two lists: `ApprovalLedger` keeps `granted`
    #: and `refused` separately and both only ever grow, which is what makes the subtraction safe.
    _prev_granted: int = field(default=0, repr=False)
    _prev_refused: int = field(default=0, repr=False)

    def begin_turn(self, message: str) -> None:
        """Tell the ledger whose words this turn is, before a single tool runs.

        ``assemble_registry`` does this at ``code_api.py:1004-1007`` and every terminal surface
        skipped it, so every fetch in a ``chat`` session would have been recorded ``unknown`` even
        once a ledger existed — and ``requester_of`` answers ``unknown`` for a ledger that was never
        told an instruction, which the narrowing treats exactly as it treats ``agent``.
        """
        self.ledger.set_instruction(message, workspace=self.workspace)

    def turn_verdicts(self) -> tuple[int, int]:
        """``(granted, refused)`` since the last call — this turn's governance decisions.

        A delta rather than a total: the ledger accumulates over the conversation, and "two calls
        were approved" is a statement about a turn, not about a session. A refusal already reaches
        the screen through ``render.refusal_lines``; a GRANT reaches it through nothing at all, and
        an approval nobody can see afterwards is a record rather than a decision.
        """
        granted = len(self.approvals.granted) - self._prev_granted
        refused = len(self.approvals.refused) - self._prev_refused
        self._prev_granted = len(self.approvals.granted)
        self._prev_refused = len(self.approvals.refused)
        return granted, refused


def _mount_mcp(registry: Any, settings: Settings) -> None:
    """Pour the connected MCP servers into the terminal's registry, as the other callers do.

    ``mcp_pool.connectors(settings)`` is called at ``code_api.py:498`` and ``main.py:2206`` and
    **nowhere in the ``chat``/``assist``/``tui`` bodies** — so a server the user connected in the
    app, and watched the Test button prove live, did not exist for the terminal even with
    ``CHIMERA_MCP_AUTOLOAD=1``. The pool is idempotent per process, so whichever surface asks first
    pays for the connect and the rest reuse it; with autoload off — the default — nothing here
    spawns anything and a stock install behaves exactly as it did.

    **Before the allowlist, deliberately**, exactly where ``assemble_registry`` and ``chimera run``
    put it: "a denylist that covers only the tools we wrote is not a denylist". Everything after
    this line — the deployment fence, the reach floor, the kernel, the taint ledger — therefore
    covers the MCP tools too, which is the whole reason this surface is a safe place to mount them.

    **In the TUI too, since the modal.** This used to say "not in the TUI", and the reason it gave
    was a real one: MCP output is untrusted content by definition (``mcp_client.py:49``), that
    surface had no ledger to fence it with and no answerable approver, so mounting it there would
    have been adding the input without the layer that reads it. The TUI builds this stack now, so
    the objection is gone rather than overruled — its MCP output arrives inside the same
    ``<<external-data>>`` fence, narrows the same run, and asks the same person. Nothing here
    distinguishes the three surfaces any more, which is the point; the gate is
    ``CHIMERA_MCP_AUTOLOAD``, off by default, and a stock install still spawns nothing.
    """
    if not settings.mcp_autoload:
        return
    from chimera.integrations import mcp_pool

    pool = mcp_pool.connectors(settings)
    if pool is None:
        return
    if settings.mcp_defer:
        from chimera.integrations.mcp_defer import register_deferred_mcp

        register_deferred_mcp(pool, registry)
    else:
        pool.into_tool_registry(registry)


def build_right_hand(
    workspace: Path,
    *,
    settings: Settings,
    surface: str,
    write_region: str | None = None,
    base: Any = None,
    ask: TerminalAsk | None = None,
) -> RightHand:
    """Assemble the terminal's governed registry, in the order ``assemble_registry`` uses.

    1. the **write region** scopes the native write tools as they are constructed;
    2. the **allowlist** — the deployment fence these surfaces already had — is applied;
    3. the owner's **reach floor** (``CHIMERA_REACH``) is unioned into the denials;
    4. the **trust kernel** wraps that, so BLOCK/REVIEW is decided before a tool can run;
    5. the **taint ledger** wraps everything, so it sees every call the kernel saw.

    ``surface`` names the caller in the audit trail. ``write_region`` is the same comma-separated
    glob string ``chimera solve --write-region`` takes; ``None`` means no region, which is what
    every terminal surface had before this existed and is therefore not a behaviour change.

    ``base`` replaces step 0 — ``default_registry`` — with a registry the caller already holds. It
    exists for one caller, ``bench/right_hand_governance/run_terminal_vs_governed.py``, and the
    reason is worth the parameter: that bench drives the injection corpus through stub tools so it
    costs US$ 0 and executes nothing, and without this seam it would have to *re-implement* steps
    1-5 to do it. A bench whose arm is a copy of the code it measures stops measuring the code the
    day the copy drifts. Everything below this line is identical either way, which is the point.

    ``ask`` is how a surface that cannot answer through stdin supplies its own question — see
    :class:`TerminalAsk`. **Both** gates take it: the host-execution confirm the shell tools consult
    as they are constructed, and the approver the kernel and the taint ledger consult per call.
    Passing it to one of the two would leave the other blocking for
    :data:`~chimera.sandbox.confirm.PROMPT_TIMEOUT_SECONDS`, which is the half-fix that measures as
    a success — the offline corpus below never touches the host-exec gate, so a bench run would
    report the governance working while every ``run_shell`` still cost two minutes.
    """
    from chimera.api.posture import deployment_posture
    from chimera.cli.main import _apply_tool_allowlist
    from chimera.governance import TaintLedger, ledger_registry, restrict_registry
    from chimera.governance.approval import ApprovalLedger, approver_for, nobody_is_at_a_terminal
    from chimera.governance.audit import AuditLog
    from chimera.governance.profile import govern_step
    from chimera.sandbox.confirm import resolve_host_exec_confirm
    from chimera.tools import default_registry
    from chimera.tools.write_region import WriteRegion

    ws = Path(workspace)
    # `WriteRegion` directly rather than `code_api.build_write_region`, which is the same three
    # lines: importing that module pulls in FastAPI, and the CLI must install and run without the
    # `desktop` extra. Blank entries are dropped and an all-blank list means "no region asked for"
    # — an empty region forbids every write, which is a thing to say on purpose and not to reach
    # through a trailing comma.
    globs = [g.strip() for g in (write_region or "").split(",") if g.strip()]
    # The host-exec gate is decided HERE, at construction, because that is where the shell and code
    # tools take it — `RunShellTool(..., confirm=confirm)`. A surface with a modal has to say so
    # before its tools exist; there is no later moment. Left unpassed, `default_registry` resolves
    # it from settings exactly as it always has, so `chat` and `assist` are byte-for-byte unchanged.
    gate = (
        {}
        if ask is None
        else {"host_exec_confirm": resolve_host_exec_confirm(settings, ask=ask.host_exec)}
    )
    registry = (
        base
        if base is not None
        else default_registry(
            ws, write_region=WriteRegion(globs, ws) if globs else None, **gate
        )
    )
    if base is None:
        _mount_mcp(registry, settings)
    # The fence these surfaces already applied, unchanged and in the same place: an explicit
    # allowlist is an instruction, and it must run before the wrappers so they wrap what survives.
    registry = _apply_tool_allowlist(registry, allow=None, deny=None, settings=settings)
    # The owner's floor, on top. Empty by default — `deployment_posture` returns no denials when
    # `CHIMERA_REACH` is unset, deliberately, because a floor is not a default. So a stock `chimera
    # chat` keeps every tool it had, including the shell, and an owner who set `read_only` finally
    # gets it honoured on the surface they are most likely to be sitting at.
    floor = deployment_posture(settings).deny_tools
    if floor:
        registry = restrict_registry(registry, allow=None, deny=floor)

    # The same file the coding turn writes and the Governance screen reads. One log, or the screen
    # shows a partial history while claiming to show the whole one.
    audit = AuditLog(Path(settings.home) / "audit.jsonl")
    approvals = ApprovalLedger()
    # A surface with its own question knows whether it can draw one; a REPL has to infer it from
    # stdin. Asking the asker is the whole difference between "refused" and "refused because there
    # was nobody to ask" on a screen where stdin is a tty and unreachable at the same time.
    attended = ask.attended if ask is not None else not nobody_is_at_a_terminal()
    # `home=None` on purpose. With a home, `approver_for` would write the question to disk and wait
    # `CHIMERA_APPROVAL_WAIT` seconds for somebody to run `chimera approve` — inside a REPL turn,
    # for a person who is sitting right there and was never shown a prompt. Without one it degrades
    # to a recorded deny, which is exactly today's headless behaviour and the thing the task asked
    # to preserve under a pipe.
    approve = approver_for(
        settings.approval_mode,
        approvals,
        home=None,
        ask_with=None if ask is None else ask.question,
    )

    # `attended=True`: unlike every other caller of this, there really is a person at this console,
    # and they are the person who asked. `audit_allows=False` for the reason `assemble_registry`
    # gives — an ALLOW per tool call would bury this log's rare events within a day.
    step = govern_step(
        registry,
        settings=settings,
        audit=audit,
        surface=surface,
        attended=True,
        audit_allows=False,
    )
    ledger = TaintLedger(authority=settings.taint_authority)
    # A union, like the denial list above and for the same reason. There is no request posture on
    # this surface, so the request half of `assemble_registry`'s expression is absent and the two
    # remaining terms are the owner's: the explicit switch and the reach/approval floor.
    narrow = bool(settings.taint_narrow or deployment_posture(settings).narrow_on_taint)
    governed = ledger_registry(
        step.registry, ledger, narrow_on_taint=narrow, audit=audit, approve=approve
    )
    return RightHand(
        registry=governed,
        ledger=ledger,
        approvals=approvals,
        workspace=ws,
        attended=attended,
    )
