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
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, params
from pydantic import BaseModel

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
    describe,
)
from chimera.api.posture import resolve as _resolve_posture
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

    posture: Posture | None = None
    """How far the agent may reach, and when it stops to ask (see :mod:`chimera.api.posture`).

    A convenience over the fields above, not a second mechanism: it resolves into ``deny_tools`` and
    the pause flags, and an explicit ``deny_tools`` is unioned with it rather than replaced — two
    ways of saying "not this tool" must never cancel each other out. None = no posture applied, so
    every existing caller behaves exactly as before."""


def resolve_posture(posture: Posture | None) -> ResolvedPosture:
    """The posture's effect, with None meaning "no posture" rather than "the default posture".

    The distinction matters at the boundary: a caller that never heard of postures must keep the
    behaviour it had, and the DEFAULT posture denies the exec tools. Silently applying it would
    break every existing client in a way that looks like the agent got worse at its job.
    """
    return _resolve_posture(posture) if posture is not None else ResolvedPosture([], False, False, False)


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
    from chimera.governance import TaintLedger, ledger_registry, restrict_registry
    from chimera.tools import default_registry

    registry = default_registry(ws, write_region=build_write_region(seams.write_region, ws))
    # Union, never replace: a posture and an explicit denylist are two ways of saying "not this
    # tool", and letting one overwrite the other means the stricter of two stated intentions loses.
    denied = sorted({*(seams.deny_tools or ()), *resolve_posture(seams.posture).deny_tools})
    if seams.allow_tools is not None or denied:
        registry = restrict_registry(registry, allow=seams.allow_tools, deny=denied or None)
    if seams.explorer:
        from chimera.core import ExploreRepositoryTool

        # A narrow localisation question does not need the worker's model, and answering it in a
        # sub-agent keeps the finding — not the search — in the main loop's context.
        registry.register(ExploreRepositoryTool(gateway, ws, max_turns=steps))
    ledger = TaintLedger()
    # A posture that asks to be told about suspicious input also arms taint-adaptive narrowing;
    # without one the env default (CHIMERA_TAINT_NARROW) still decides, as it always did.
    narrow = (
        resolve_posture(seams.posture).narrow_on_taint
        if seams.posture is not None
        else settings.taint_narrow
    )
    return ledger_registry(registry, ledger, narrow_on_taint=narrow), ledger


class PostureQuery(BaseModel):
    """Ask what a posture would mean, without committing to it — the selectors' live preview."""

    reach: Reach = DEFAULT_REACH
    approval: Approval = DEFAULT_APPROVAL
    workspace: str | None = None


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
    app: FastAPI, guard: params.Depends, workspace: Path, settings: Settings
) -> None:
    """Mount ``POST /api/code/turn`` — a conversational coding turn, streamed."""
    from chimera.core.code_session import CodeSession, CodeSessionStore
    from chimera.core.events import tool as tool_event

    store = CodeSessionStore(settings.home / "code_sessions")
    # One lock per session: two concurrent turns on the same conversation would interleave their
    # transcripts and the last save would silently win. Different sessions never wait on each other.
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def lock_for(session_id: str) -> threading.Lock:
        with locks_guard:
            return locks.setdefault(session_id, threading.Lock())

    def build_agent(req: CodeTurnRequest, ws: Path) -> Agent:
        from chimera.core import Agent, AgentConfig
        from chimera.providers import LLMGateway

        gateway = LLMGateway()
        steps = resolve_steps(req.max_steps)
        registry, _ledger = assemble_registry(req, ws, settings, gateway, steps=steps)
        agent = Agent(
            gateway,
            registry,
            AgentConfig(
                model=req.model,
                max_steps=steps,
                context_budget=req.context_budget,
                project_root=ws,
                trace_path=settings.home / "traces.jsonl",
            ),
        )
        if req.open_file:
            # Path only, never content: what a compaction must restore is *which* file is being
            # worked on. Re-reading it is the agent's job and it has a tool for that; a copy taken
            # at turn start would be restored stale, which is worse than restoring nothing.
            agent.run_state.open_file = (req.open_file, "")
        return agent

    @app.post("/api/code/turn", dependencies=[guard])
    async def code_turn(req: CodeTurnRequest) -> EventSourceResponse:
        # SAFETY POSTURE: identical to the run endpoint — file writes and shell inside ``ws``, behind
        # the bearer guard and the localhost bind, scoped by whatever seams the caller declared.
        ws = Path(req.workspace).expanduser().resolve() if req.workspace else workspace
        agent = build_agent(req, ws)
        session = store.load(req.session_id, agent) if req.session_id else CodeSession(agent)
        session.agent = agent  # a loaded session carries messages, not the agent that made them
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

        def on_edit(path: str, patch: str) -> None:
            emit("edit", {"path": path, "patch": patch})

        def work() -> None:
            try:
                with lock_for(session_id):
                    result = session.send(
                        req.message,
                        on_token=on_token if req.stream else None,
                        on_tool=on_tool,
                        on_edit=on_edit,
                    )
                    store.save(session)
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
        return describe(Posture(reach=req.reach, approval=req.approval), ws, settings)

    @app.delete("/api/code/sessions/{session_id}", dependencies=[guard])
    def delete_code_session(session_id: str) -> dict[str, bool]:
        """Forget a conversation. An unknown id is ``{ok: false}`` with a 200, not a 404 — that is
        exactly the state a second click on Clear hits, and it is not an error."""
        try:
            return {"ok": store.delete(session_id)}
        except ValueError:
            return {"ok": False}
