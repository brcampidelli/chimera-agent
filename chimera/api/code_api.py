"""The coding surface: the seams a coding agent needs, and the conversation that uses them.

Two things live here.

**The seams** (:class:`CodeSeams`) are the per-run knobs that decide how far a coding loop may go
and what it may touch. They are shared by the autonomous run endpoint and the conversational one
rather than declared twice, because :func:`assemble_registry` builds the tool registry in an order
that is *load-bearing* — the write region scopes the native write tools, the allowlist lands before
the meta-tools so a sub-agent inherits it, and the taint ledger stays outermost so it sees every
call. Two copies of that order is one copy waiting to drift, and the way it would drift is silent:
an allowlist applied one line too late still looks applied.

**The conversation** (``POST /api/code/turn``) is what the Code screen needed and the run endpoint
could not be. A run is a closed transaction — plan, execute, verify, revert, receipt — and that is
right for "make the tests pass" and wrong for "what does this module do?", "ok, rename it", "no, the
other one". Those are turns, and they need the previous turn's tool calls, which is exactly what
:class:`~chimera.core.code_session.CodeSession` keeps and the prose-flattening chat session cannot.

The two are not alternatives. The conversation is the default because it is fast and cheap; the run
is the button you press when the change is worth verifying. They share this file's seams so that
pressing that button does not change what the agent is allowed to do.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, File, HTTPException, UploadFile, params
from pydantic import BaseModel, Field

# Module level, not inside the registration function, and that is load-bearing rather than tidiness:
# this file uses `from __future__ import annotations`, so a `-> EventSourceResponse` return
# annotation is a string FastAPI resolves against THIS module's globals when it builds the OpenAPI
# schema. Imported locally, the name is absent there and `python -m chimera.api.schema_dump` dies
# with a PydanticUserError about an undefined ForwardRef — at schema-generation time, long after the
# tests that exercise the endpoint have all passed.
from sse_starlette.sse import EventSourceResponse

from chimera.api.posture import (
    DEFAULT_APPROVAL,
    DEFAULT_REACH,
    Approval,
    Posture,
    PostureFacts,
    Reach,
    ResolvedPosture,
    deployment_posture,
    describe,
)
from chimera.api.posture import resolve as _resolve_posture
from chimera.api.roles import Profile, RoleModels, RolePlan
from chimera.api.roles import resolve as resolve_roles
from chimera.api.schemas import (
    AttachmentOut,
    CodeSessionMetaOut,
    CodeSessionOut,
    DictationOut,
    TranscriptOut,
    VisionOut,
)
from chimera.api.worth import WorthReport, summarize_worth
from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings
    from chimera.core.agent import Agent
    from chimera.tools import ToolRegistry

_log = get_logger("api.code")

#: Hard ceiling on a requested step count. Not a judgement about how many steps a task needs — it
#: is the difference between a long run and a runaway one, and the client asking is a UI field.
MAX_RUN_STEPS = 100


class CodeSeams(BaseModel):
    """How far a coding loop may go, and what it may touch.

    Everything here is opt-in and defaults to the behaviour these endpoints already had. They exist
    because the run endpoint was built as a deliberate minimum and the desktop's Code screen then
    inherited that minimum as a ceiling — a run started from the app was structurally weaker than
    the same run started from a terminal, for no reason anyone had written down.
    """

    max_steps: int | None = None
    """Tool-loop steps the worker may take per turn. None = the agent's own default.

    This field replaces a hard-coded 6 that had no comment justifying it while ``AgentConfig``
    documents 8 — so a run through the API was capped lower than the identical run through
    ``chimera solve``, and nothing said so. Clamped to 1..MAX_RUN_STEPS: a client asking for ten
    thousand steps is asking for a bill, not a run."""

    context_budget: float | None = None
    """Fraction of the model's advertised window to spend on the prompt before compacting.

    None (default) keeps the historical behaviour: the message list only grows and an overflow is
    terminal. This pairs with ``max_steps`` and should usually move with it — raising the step
    ceiling without a budget raises the chance of dying on overflow instead of finishing."""

    repo_map: bool = False
    """Prepend a bounded structural digest of the repository, ranked by importance, so the agent can
    aim at the right file instead of exploring blind (mirrors ``chimera solve --repo-map``)."""

    explorer: bool = False
    """Give the agent an isolated read-only Context Explorer for repository search, so localisation
    ("where does X live?") costs a cheap sub-agent rather than turns of the main loop."""

    allow_tools: list[str] | None = None
    """Session allowlist of tool names. None = every tool. An explicit list — *including an empty
    one* — is an allowlist, so ``[]`` is a fully locked, read-nothing session."""

    deny_tools: list[str] | None = None
    """Tool names removed from the session even when allowed (deny wins over allow)."""

    write_region: list[str] | None = None
    """Globs the write tools are confined to, relative to the workspace. None = the whole workspace
    (fenced by ``WorkspaceGuard`` as before). Fail-closed: a write outside the region is refused."""

    profile: Profile | None = None
    """Which tier each ROLE draws from: economy / balanced / max (see :mod:`chimera.api.roles`).

    None = every role runs on the request's own ``model``, which is what every caller got before
    roles existed. A profile is a starting point, never a ceiling — ``roles`` overrides it field by
    field."""

    roles: RoleModels | None = None
    """Per-role model overrides, merged over the profile. Only the fields actually sent are applied,
    so a partial override cannot blank the rest of the profile back to the default model."""

    attachments: list[str] = Field(default_factory=list)
    """Ids from ``POST /api/attachments`` — images for the model to look at, documents for it to read.

    Ids rather than inline data: a base64 image in the request body would be re-sent on every retry
    and logged wherever the request is logged. They belong to THIS turn only, and are not stored with
    the conversation — re-sending a picture on every later turn costs again for no new information.
    """

    fuse: bool = False
    """Route THIS turn through the fusion panel.

    A fused turn CANNOT call tools: ``FusionEngine.complete`` drops the tool schemas, so the agent
    finishes in one step having touched nothing and answers from the prompt alone. In a coding
    conversation that is a sharper edge than in a chat — ask it to fix a file and it will describe a
    fix it never applied. It is offered anyway, because a deliberative question ("which of these two
    designs is better?") is exactly what a panel is good at, and refusing to offer it would be
    deciding for the user. What is NOT optional is that the turn says so, which ``done.fused`` does.
    """

    posture: Posture | None = None
    """How far the agent may reach, and when it stops to ask (see :mod:`chimera.api.posture`).

    A convenience over the fields above, not a second mechanism: it resolves into ``deny_tools`` and
    the pause flags, and an explicit ``deny_tools`` is unioned with it rather than replaced — two
    ways of saying "not this tool" must never cancel each other out. None = no posture applied, so
    every existing caller behaves exactly as before."""


#: Snapshots kept so a failed editing turn can be undone if the user asks. Bounded and in-memory: an
#: offer that outlives the app is an offer nobody remembers making, and persisting whole-workspace
#: snapshots to disk to support one button is a much larger promise than this button makes.
_pending_reverts: dict[str, tuple[Any, Any]] = {}
_MAX_PENDING_REVERTS = 8

#: FastAPI's upload marker, hoisted out of the signatures so a call in an argument default does not
#: trip the linter. Same object, same behaviour.
_UPLOAD = File(...)


def resolve_posture(posture: Posture | None) -> ResolvedPosture:
    """The posture's effect, with None meaning "no posture" rather than "the default posture".

    The distinction matters at the boundary: a caller that never heard of postures must keep the
    behaviour it had, and the DEFAULT posture denies the exec tools. Silently applying it would
    break every existing client in a way that looks like the agent got worse at its job.
    """
    return _resolve_posture(posture) if posture is not None else ResolvedPosture([], False, False, False)


def resolve_role_plan(seams: CodeSeams, settings: Settings) -> RolePlan:
    """The roles this request runs with. No profile and no overrides = no role routing at all."""
    return resolve_roles(seams.profile, settings, seams.roles)


def clamp_steps(value: int | None) -> int | None:
    """Clamp a requested step ceiling into 1..MAX_RUN_STEPS. None (not asked) stays None."""
    if value is None:
        return None
    return max(1, min(MAX_RUN_STEPS, value))


def resolve_steps(value: int | None) -> int:
    """The step ceiling actually used: the caller's, clamped, or the agent's documented default."""
    from chimera.core import AgentConfig

    clamped = clamp_steps(value)
    return clamped if clamped is not None else AgentConfig.max_steps


def build_write_region(globs: list[str] | None, ws: Path) -> Any:
    """Build a ``WriteRegion`` from the requested globs, or None when the caller named none.

    Blank entries are dropped, and a list that contained nothing but blanks is treated as "no region
    asked for" rather than as an empty region — an empty region forbids every write, which is a
    thing a caller should have to say on purpose, not stumble into via a trailing comma.
    """
    from chimera.tools.write_region import WriteRegion

    cleaned = [g.strip() for g in (globs or []) if g.strip()]
    return WriteRegion(cleaned, ws) if cleaned else None


def _intersect_allow(request: list[str] | None, deployment: list[str] | None) -> list[str] | None:
    """Two allowlists, intersected — never replaced.

    ``None`` means "no allowlist here"; an explicit list, *including an empty one*, is a real one.

    Intersection rather than precedence, because the two lists are not peers. The deployment's list
    is the owner's ceiling, set once in the environment; the request's is one caller's ask. If a
    request could replace the ceiling, an owner's restriction would be removable by whoever sends
    the request — the wrong direction for the one control here that can only ever take capability
    away. The CLI keeps its own precedence rule (a typed flag beats the env) because there the
    person typing owns both.
    """
    if request is None:
        return deployment
    if deployment is None:
        return request
    return sorted(set(request) & set(deployment))


def assemble_registry(
    seams: CodeSeams,
    ws: Path,
    settings: Settings,
    gateway: Any,
    *,
    steps: int,
) -> tuple[ToolRegistry, Any]:
    """Build the tool registry for a coding turn, and the taint ledger watching it.

    The order mirrors the CLI's ``_run_solve`` and each step depends on the one before:

    1. the **write region** scopes the native write tools as they are constructed;
    2. the **allowlist** is applied BEFORE the meta-tools, so a sub-agent inherits it;
    3. the **explorer** is registered into that already-scoped registry;
    4. the **taint ledger** wraps everything, so it sees every call including the sub-agent's.

    Returns the registry and the ledger, because the same ledger has to reach the agent as ``taint=``
    — that is what lets a run know it read untrusted content and is therefore pausable. Building two
    would mean the run that got tainted and the run that gets asked about it are different objects,
    and the pause would never fire.
    """
    from chimera.core import ExploreRepositoryTool
    from chimera.governance import TaintLedger, ledger_registry, restrict_registry
    from chimera.governance.audit import AuditLog
    from chimera.tools import default_registry

    registry = default_registry(ws, write_region=build_write_region(seams.write_region, ws))
    # Union, never replace: a posture, an explicit denylist and the deployment's own denylist are
    # three ways of saying "not this tool", and letting one overwrite another means the strictest of
    # several stated intentions loses.
    #
    # CHIMERA_TOOL_DENYLIST / _ALLOWLIST reach the app for the first time here. They were read by
    # `chimera run` and `chimera solve` and by nothing else, so setting them and then using the
    # desktop app or the API restricted exactly nothing. A variable named like a security control
    # that controls nothing is worse than no variable at all: it reads as a fence in `.env` while
    # every request runs unfenced, and it fails in the direction nobody checks.
    denied = sorted({
        *(seams.deny_tools or ()),
        *resolve_posture(seams.posture).deny_tools,
        *deployment_posture(settings).deny_tools,
        *settings.tool_denylist,
    })
    allowed = _intersect_allow(seams.allow_tools, settings.tool_allowlist or None)
    if allowed is not None or denied:
        registry = restrict_registry(registry, allow=allowed, deny=denied or None)
    # Checked, because this one is registered AFTER the filter ran and would otherwise be the one
    # tool no list could touch. Against the DEPLOYMENT allowlist and the union of every denial —
    # deliberately not against the request's own `allow_tools`: a caller that sets `explorer=True`
    # alongside its own list is granting itself the tool by another field, which is its right, while
    # an owner's ceiling must not be raisable by a request at all. And unlike `spawn_subagent`, this
    # tool does not inherit the restricted registry — it builds its own read-only set internally, so
    # letting it through was granting a capability, not just a name.
    explorer_ok = ExploreRepositoryTool.name not in denied and (
        not settings.tool_allowlist or ExploreRepositoryTool.name in settings.tool_allowlist
    )
    if seams.explorer and explorer_ok:
        # A narrow localisation question does not need the worker's model, and answering it in a
        # sub-agent keeps the finding — not the search — in the main loop's context.
        # A cheap model for the explorer is the point of the role: a narrow localisation
        # question does not need the editor's model, and the sub-agent returns the finding rather
        # than the search, so the main loop never pays for the hunt.
        explore_model = resolve_role_plan(seams, settings).models.explore
        registry.register(
            ExploreRepositoryTool(gateway, ws, model=explore_model, max_turns=steps)
        )
    ledger = TaintLedger()
    # A posture that asks to be told about suspicious input also arms taint-adaptive narrowing;
    # without one the env default (CHIMERA_TAINT_NARROW) still decides, as it always did.
    narrow = (
        resolve_posture(seams.posture).narrow_on_taint
        if seams.posture is not None
        else settings.taint_narrow
    ) or deployment_posture(settings).narrow_on_taint
    # The audit trail starts here, and only here. `LedgeredTool` records the three things worth a
    # permanent line — a dangerous tool narrowed after untrusted input, a call escalated for review,
    # a side effect suppressed as a duplicate — and each is rare by construction.
    #
    # Deliberately NOT passed to `restrict_registry` above: the default posture excludes the exec
    # tools on every single turn, so that would append an identical entry per turn and bury the
    # events someone opens this log to find. A trail nobody can read is the same as no trail.
    #
    # Until now the app wrote nothing here at all, which made the Security screen's empty state mean
    # "nothing is recording" while it read as "nothing has happened". Those are opposite claims.
    return ledger_registry(
        registry,
        ledger,
        narrow_on_taint=narrow,
        audit=AuditLog(settings.home / "audit.jsonl"),
    ), ledger


class PostureQuery(BaseModel):
    """Ask what a posture would mean, without committing to it — the selectors' live preview."""

    surface: str = "run"
    """Which surface is asking: ``"run"``, ``"turn"`` or ``"chat"``.

    The same posture means different things on each, and only one of them was ever reported. A
    conversational turn is built without a checkpointer, so it cannot stop and ask — whatever the
    approval axis says. A CHAT is built without the ledger too unless ``CHIMERA_GUARD_CHAT`` is on,
    which means nothing marks it after it reads untrusted content and the tools that would start
    refusing keep working. Defaulting to ``"run"`` keeps every existing caller reading exactly what
    it read before."""

    reach: Reach = DEFAULT_REACH
    approval: Approval = DEFAULT_APPROVAL
    workspace: str | None = None


class RolesQuery(BaseModel):
    """Ask which model each role would run on, without committing to it."""

    profile: Profile = "balanced"


class CodeTurnRequest(CodeSeams):
    """One turn of a coding conversation."""

    message: str
    session_id: str | None = None
    """The conversation this turn belongs to. None mints a new one, returned in the first frame."""
    workspace: str | None = None
    model: str | None = None
    stream: bool = True
    open_file: str | None = None
    """The file the user has open, workspace-relative. Two effects, both real: it focuses which
    ``AGENTS.md`` files apply, and it is what a compaction restores. Not read here — the agent has
    tools for that, and a server-side read would put a stale copy in the prompt."""


def register_code_api(
    app: FastAPI,
    guard: params.Depends,
    workspace: Path,
    settings: Settings,
    *,
    live_settings: Callable[[], Settings] | None = None,
    memory: Any = None,
    graph: Any = None,
    fuse_backend: Any = None,
) -> None:
    """Mount ``POST /api/code/turn`` — a conversational coding turn, streamed.

    ``memory``/``graph`` are READ from, never written to. Reading is safe in a way writing is not: a
    recalled fact learned from untrusted content carries its ``[unverified]`` label into the prompt
    and the admission gate still rejects injection patterns, so the worst case is a wasted line of
    context. Writing is the opposite — with the workspace trusted by default, a poisoned README would
    enter memory looking clean, and a memory item has no project scope, so it would be recalled into
    every other project too. That asymmetry is the whole reason this is one-way.
    """
    from chimera.core.code_session import CodeSession, CodeSessionStore
    from chimera.core.events import tool as tool_event
    from chimera.core.instructions import load as load_identity
    from chimera.core.instructions import render as render_identity
    from chimera.interface.session import recall_facts

    # `settings` is the snapshot taken when the routes were mounted. It is the right thing for
    # `home` — the data directory must not move under an open conversation — and the wrong thing for
    # anything the Settings screen can change, which is why those read through `live()`. Without a
    # reader supplied (a test mounting its own Settings), the snapshot IS the answer: an explicit
    # injection means "use THIS one".
    live: Callable[[], Settings] = live_settings or (lambda: settings)

    store = CodeSessionStore(settings.home / "code_sessions")
    # One lock per session: two concurrent turns on the same conversation would interleave their
    # transcripts and the last save would silently win. Different sessions never wait on each other.
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def lock_for(session_id: str) -> threading.Lock:
        with locks_guard:
            return locks.setdefault(session_id, threading.Lock())

    def build_agent(req: CodeTurnRequest, ws: Path, facts: list[str]) -> tuple[Agent, Any]:
        """The agent for this turn, and the ledger watching it.

        The ledger used to be built and thrown away, which left the turn unable to answer the one
        question the posture claims to care about: did this run read untrusted content? Narrowing
        still worked (it lives inside the wrapped tools), but nothing could REPORT it — so a turn
        that had been steered by a planted instruction looked exactly like one that had not.
        """
        from chimera.core import Agent, AgentConfig
        from chimera.providers import LLMGateway

        gateway = LLMGateway()
        steps = resolve_steps(req.max_steps)
        registry, ledger = assemble_registry(req, ws, live(), gateway, steps=steps)
        # Recalled facts ride in the SYSTEM prompt, and that placement is load-bearing: `absorb`
        # drops system messages when it stores the transcript, so the recall is refreshed each turn
        # instead of accumulating stale copies of itself in the conversation forever.
        from chimera.core.agent import DEFAULT_SYSTEM_PROMPT

        system_prompt = DEFAULT_SYSTEM_PROMPT
        if facts:
            system_prompt += "\n\nRelevant facts from memory:\n" + "\n".join(
                f"- {f}" for f in facts
            )
        agent = Agent(
            gateway,
            registry,
            AgentConfig(
                model=req.model,
                system_prompt=system_prompt,
                max_steps=steps,
                context_budget=req.context_budget,
                project_root=ws,
                # Read per turn, so editing the identity applies to the next question rather than
                # to the next launch.
                instructions=render_identity(load_identity(settings.home)),
                trace_path=settings.home / "traces.jsonl",
            ),
        )
        if req.open_file:
            # Path only, never content: what a compaction must restore is *which* file is being
            # worked on. Re-reading it is the agent's job and it has a tool for that; a copy taken
            # at turn start would be restored stale, which is worse than restoring nothing.
            agent.run_state.open_file = (req.open_file, "")
        return agent, ledger

    @app.post("/api/code/turn", dependencies=[guard])
    async def code_turn(req: CodeTurnRequest) -> EventSourceResponse:
        # SAFETY POSTURE: identical to the run endpoint — file writes and shell inside ``ws``, behind
        # the bearer guard and the localhost bind, scoped by whatever seams the caller declared.
        # Validated the same way the read-only fs endpoints and POST /api/runs validate theirs. A
        # path that does not exist used to be accepted silently, and the turn would run against a
        # directory the agent then created files in — the one axis on which this endpoint was more
        # permissive than the chat it is replacing.
        if req.workspace:
            ws = Path(req.workspace).expanduser().resolve()
            if not ws.is_dir():
                raise HTTPException(status_code=400, detail="workspace not found")
        else:
            ws = workspace
        # Attachments: images the model looks at, documents it reads. Resolved here rather than in
        # the request so a stale or forged id is simply skipped instead of reaching the model.
        from chimera.api.attachments import load as load_attachment

        images: list[str] = []
        doc_blocks: list[str] = []
        for ident in req.attachments:
            found = load_attachment(settings.home, ident)
            if found is None:
                continue
            if found.kind == "image":
                images.append(str(found.path))
            else:
                # Extracted at upload and fenced there. Folded into the message rather than handed
                # over as a file path, so it works with every model — including the ones that cannot
                # see — and so the user does not need the document tool installed to attach a PDF.
                from chimera.api.attachments import save as _save

                text = _save(settings.home, found.name, found.path.read_bytes()).text
                if text:
                    doc_blocks.append(f"Attached document `{found.name}`:\n{text}")

        message = req.message
        if doc_blocks:
            message = message + "\n\n" + "\n\n".join(doc_blocks)

        # Read memory BEFORE building the agent: the facts go into this turn's system prompt.
        facts, memory_layer = recall_facts(req.message, memory=memory, graph=graph)
        agent, ledger = build_agent(req, ws, facts)
        session = store.load(req.session_id, agent) if req.session_id else CodeSession(agent)
        session.agent = agent  # a loaded session carries messages, not the agent that made them
        # A conversation belongs to the project it STARTED in, and keeps it. Overwriting on every
        # turn would let a session drift between projects in the sidebar as the user switches
        # around, so an old conversation would file itself under whatever codebase happened to be
        # selected when it was last reopened — which is the one thing a grouped list must not do.
        if not session.workspace:
            session.workspace = req.workspace or ""
        session_id = session.session_id

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any] | None] = asyncio.Queue()

        def emit(event: str, payload: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def on_token(text: str) -> None:
            emit("token", {"text": text})

        def on_tool(activity: Any) -> None:
            # Reuses the run channel's event builder, so a tool call looks the same whichever
            # endpoint produced it — and is clipped by the same rules, which SAY they clipped.
            emit("tool", tool_event(activity.name, activity.arguments, activity.ok,
                                    activity.observation).data)

        edited: list[str] = []

        def on_edit(path: str, patch: str) -> None:
            edited.append(path)
            emit("edit", {"path": path, "patch": patch})

        def work() -> None:
            try:
                # Taken BEFORE the turn, so a turn that edits can be judged and undone like a run.
                #
                # Until now the two buttons on this screen were not two ways of doing one thing: Send
                # edited your files and kept whatever it wrote, while "Run with verification" planned,
                # verified, and reverted on failure. Nothing on the screen said so. Collapsing them to
                # one button would have made pressing Enter silently the weaker of the two, so the
                # weaker one had to stop being weaker first.
                from chimera.core.checkpoint import WorkspaceGuard

                guard = WorkspaceGuard(ws)
                before = guard.snapshot()

                # Same swap the chat turn uses, under the same per-session lock: hand the agent the
                # fusion engine for this call and restore it in `finally`. Fusion ignores tools, so
                # this turn will not touch a file — reported as `fused` rather than left to look like
                # a turn that simply did not need to.
                fused = bool(req.fuse) and fuse_backend is not None
                original_backend = getattr(agent, "backend", None) if fused else None
                if fused:
                    agent.backend = fuse_backend  # type: ignore[assignment]
                with lock_for(session_id):
                    result = session.send(
                        message,
                        # Fusion has no token stream (the engine only implements `complete`), so
                        # asking for one would leave the screen blank until the whole panel lands.
                        on_token=on_token if (req.stream and not fused) else None,
                        on_tool=on_tool,
                        on_edit=on_edit,
                        images=images or None,
                    )
                    if fused:
                        agent.backend = original_backend  # type: ignore[assignment]
                    store.save(session)
                # Judge what the turn wrote, if it wrote anything and the project says how.
                if edited:
                    from chimera.api.app import resolve_verify
                    from chimera.core.verify import CommandVerifier

                    command, source = resolve_verify(None, ws)
                    if command is None:
                        # Said out loud rather than left as an absence: an editing turn that nothing
                        # checked is exactly the case a user would otherwise assume was checked,
                        # because the other button on this screen does check.
                        emit("verified", {"command": None, "source": source, "state": "none"})
                    else:
                        outcome = CommandVerifier(command, ws).verify()
                        state = (
                            "abstained" if outcome.abstained
                            else "passed" if outcome.passed
                            else "failed"
                        )
                        token = ""
                        if state == "failed":
                            # OFFERED, not applied. A run reverts itself because the user asked for a
                            # verdict; a conversation is a conversation, and silently undoing what
                            # someone watched being typed is a worse surprise than a failing test.
                            token = uuid.uuid4().hex
                            _pending_reverts[token] = (guard, before)
                            while len(_pending_reverts) > _MAX_PENDING_REVERTS:
                                _pending_reverts.pop(next(iter(_pending_reverts)))
                        emit("verified", {
                            "command": command, "source": source, "state": state,
                            "output": outcome.output[:4000], "revert_token": token,
                        })
                emit(
                    "done",
                    {
                        "answer": result.answer,
                        "steps": result.steps,
                        "stopped_reason": result.stopped_reason,
                        "tool_names": list(result.tool_names),
                        "model": result.model,
                        "prompt_tokens": result.prompt_tokens,
                        "completion_tokens": result.completion_tokens,
                        "usd": result.usd,
                        # The number that says whether raising max_steps is safe. Reported because
                        # a ceiling the user can raise without seeing its cost is a trap.
                        "context_peak_tokens": result.steplog.context_peak_tokens,
                        "route_meta": result.route_meta,
                        # Did this turn read anything untrusted? A turn steered by a planted
                        # instruction used to be indistinguishable from one that was not.
                        "tainted": bool(ledger.run_tainted()),
                        # What the conversation remembered, and from which layer. A coding turn used
                        # to accumulate nothing and recall nothing; it now READS what the chat reads.
                        "memory_facts_used": len(facts),
                        "memory_layer": memory_layer,
                        # This turn could not use tools, and a reader cannot tell that from a zero
                        # tool count alone — the same count a turn that needed none reports.
                        "fused": fused,
                    },
                )

            except Exception as exc:  # noqa: BLE001 — surfaced to the client as an error event
                _log.warning("code turn failed: %s", exc)
                emit("error", {"message": "the coding turn failed"})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel: end of stream

        threading.Thread(target=work, daemon=True).start()

        async def events() -> AsyncIterator[dict[str, str]]:
            # The session id first, so a client that minted a new conversation can address it from
            # the very first frame rather than after the turn it is already watching.
            yield {"event": "session", "data": json.dumps({"session_id": session_id})}
            while True:
                item = await queue.get()
                if item is None:
                    break
                event, payload = item
                yield {"event": event, "data": json.dumps(payload)}

        return EventSourceResponse(events())

    @app.post("/api/code/posture", dependencies=[guard], response_model=PostureFacts)
    def code_posture(req: PostureQuery) -> PostureFacts:
        """What the chosen posture MEANS on this machine, right now.

        A POST rather than a GET because it reports the live state of the sandbox rather than a
        stored resource, and because caching this answer is precisely the bug: a Docker daemon that
        died since the last call must change the answer, not be served from a cache.
        """
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        return describe(
            Posture(reach=req.reach, approval=req.approval),
            ws,
            settings,
            can_pause=req.surface == "run",
            # The coding turn is always assembled with a ledger. A chat only is when the user armed
            # it — and when they have not, the sentence has to say so, because the whole product
            # rests on stating what is true on this machine rather than what reads better.
            guarded=req.surface != "chat" or live().guard_chat,
        )

    @app.post("/api/code/roles", dependencies=[guard], response_model=RoleModels)
    def code_roles(req: RolesQuery) -> RoleModels:
        """The concrete model slugs a profile resolves to, so the UI can show them.

        Resolved server-side rather than mirrored in the frontend: the tiers already honour the
        user's cost mode and explicit per-tier settings, and a second copy of that resolution in
        TypeScript would display a model the run does not actually use.
        """
        return resolve_roles(req.profile, settings).models

    @app.get("/api/code/worth", dependencies=[guard], response_model=WorthReport)
    def code_worth() -> WorthReport:
        """What each configuration cost and got, over the runs that really happened here.

        Read from the same append-only run log the receipts come from — no separate store to drift
        out of sync with the evidence it summarises.
        """
        from chimera.api.runs import load_runs

        return summarize_worth(load_runs(settings.home / "runs.jsonl"))

    @app.post("/api/attachments", dependencies=[guard], response_model=AttachmentOut)
    async def upload_attachment(file: UploadFile = _UPLOAD) -> AttachmentOut:
        """Take one file for the agent to look at or read.

        The response carries an id, never the content. Documents are converted to text HERE rather
        than at turn time, so an unreadable PDF or a missing optional dependency surfaces while the
        user is still looking at the attach button, instead of halfway through a turn they are
        paying for. Images are stored as-is; the gateway encodes them into the request.
        """
        from chimera.api.attachments import save as save_attachment

        data = await file.read()
        try:
            saved = save_attachment(settings.home, file.filename or "file", data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return AttachmentOut(
            id=saved.id, name=saved.name, kind=saved.kind, chars=len(saved.text), note=saved.note
        )

    @app.get("/api/vision", dependencies=[guard], response_model=VisionOut)
    def vision() -> VisionOut:
        """Can the model that answers a turn look at an image?

        Asked when someone attaches one, so the answer arrives while they can still act on it. An
        image sent to a model without vision is either a provider error or — worse — silently
        ignored, and a confident answer about a picture nobody looked at is indistinguishable from
        one about a picture that was.
        """
        from chimera.api.attachments import vision_support

        model = live().default_model
        return VisionOut(model=model, support=vision_support(model))

    @app.get("/api/dictation", dependencies=[guard], response_model=DictationOut)
    def dictation() -> DictationOut:
        """Can speech become text here? Asked before the microphone opens, not after."""
        from chimera.api.attachments import dictation_support

        support, how = dictation_support(settings)
        return DictationOut(support=support, how=how)

    @app.post("/api/transcribe", dependencies=[guard], response_model=TranscriptOut)
    async def transcribe_audio(file: UploadFile = _UPLOAD) -> TranscriptOut:
        """Speech to text, for dictating a message instead of typing it.

        Runs through the same tool the agent uses — a local model when the `stt` extra is installed,
        the API otherwise. Deliberately not a second implementation: a person dictating and an agent
        transcribing a recording must not be able to get different answers, or to have one path work
        while the other is quietly unconfigured.
        """
        from chimera.api.attachments import save as save_attachment
        from chimera.api.attachments import transcribe

        saved = save_attachment(settings.home, file.filename or "speech.webm", await file.read())
        text = transcribe(saved.path)
        # The tool reports its own failures as text rather than raising. Passing "error: ..." into
        # the composer as if it were dictation is the one outcome worth intercepting.
        if text.lower().startswith("error"):
            return TranscriptOut(text="", note=text)
        return TranscriptOut(text=text.strip(), note="")

    @app.get("/api/code/sessions", dependencies=[guard], response_model=list[CodeSessionMetaOut])
    def list_code_sessions() -> list[dict[str, Any]]:
        """Past coding conversations, newest first, each carrying the project it belongs to.

        The list is what makes a sidebar possible: without the project on each row, past
        conversations are a flat pile you cannot file. Titles are the first thing the user asked,
        derived on read — never generated, so a row is never a paraphrase of the conversation it
        points at.
        """
        return store.list_meta()

    @app.get("/api/code/sessions/{session_id}", dependencies=[guard], response_model=CodeSessionOut)
    def get_code_session(session_id: str) -> dict[str, Any]:
        """A stored conversation as the exchanges a person had, ready to render.

        Resuming used to show an empty screen while the agent silently carried the whole history —
        the worst of both, because the next question worked for reasons the user could not see.

        An unknown id returns an empty conversation rather than a 404: the store treats a missing
        file as the ordinary first-turn case, and a screen that errors on a session someone just
        deleted in another window would be reporting a race as a fault.
        """
        from chimera.api.code_replay import exchanges_from_messages

        path = store._path(session_id)
        if not path.is_file():
            return {"id": session_id, "workspace": "", "exchanges": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            messages = [m for m in data.get("messages", []) if isinstance(m, dict)]
        except (OSError, ValueError):
            return {"id": session_id, "workspace": "", "exchanges": []}
        return {
            "id": session_id,
            "workspace": str(data.get("workspace") or ""),
            "exchanges": exchanges_from_messages(messages),
        }

    @app.post("/api/code/revert/{token}", dependencies=[guard])
    def revert_turn(token: str) -> dict[str, Any]:
        """Undo an editing turn whose verification failed, if the user takes the offer.

        A token is single-use and dies with the process. An unknown one is ``{ok: false}`` rather
        than a 404 — that is the state a second click hits, and a stale offer is not an error.
        """
        pending = _pending_reverts.pop(token, None)
        if pending is None:
            return {"ok": False, "restored": 0}
        workspace_guard, snapshot = pending
        return {"ok": True, "restored": workspace_guard.restore(snapshot)}

    @app.delete("/api/code/sessions/{session_id}", dependencies=[guard])
    def delete_code_session(session_id: str) -> dict[str, bool]:
        """Forget a conversation. An unknown id is ``{ok: false}`` with a 200, not a 404 — that is
        exactly the state a second click on Clear hits, and it is not an error."""
        try:
            return {"ok": store.delete(session_id)}
        except ValueError:
            return {"ok": False}
